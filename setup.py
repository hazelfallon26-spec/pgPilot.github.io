"""Module 2: PG details, floors, rooms, beds, rent/deposit, rules, staff + backup contacts.
Money is stored as whole paise (integers) so rupee maths is never off by a fraction."""
from core import connect as _connect, audit, require_owner, normalize_phone, AuthError

SCHEMA = """
CREATE TABLE IF NOT EXISTS pg_settings(pg_id INTEGER PRIMARY KEY REFERENCES pgs(id),
  address TEXT, phone TEXT, basic_rules TEXT, food_available INTEGER, meal_info TEXT,
  rent_rules TEXT, guest_rules TEXT, guest_max_nights INTEGER, rent_due_day INTEGER,
  late_fee_enabled INTEGER NOT NULL DEFAULT 0, backup_name TEXT, backup_phone TEXT);
CREATE TABLE IF NOT EXISTS floors(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL REFERENCES pgs(id),
  number INTEGER NOT NULL CHECK(number>=0), UNIQUE(pg_id,number));
CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL REFERENCES pgs(id),
  floor_id INTEGER NOT NULL REFERENCES floors(id), room_number TEXT NOT NULL, room_type TEXT,
  capacity INTEGER NOT NULL CHECK(capacity IN(1,2)), rent_paise INTEGER NOT NULL CHECK(rent_paise>=0),
  deposit_paise INTEGER NOT NULL CHECK(deposit_paise>=0),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','maintenance','closed')),
  UNIQUE(pg_id,room_number));
CREATE TABLE IF NOT EXISTS beds(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL REFERENCES pgs(id),
  room_id INTEGER NOT NULL REFERENCES rooms(id), label TEXT NOT NULL,
  rent_paise INTEGER NOT NULL CHECK(rent_paise>=0), deposit_paise INTEGER NOT NULL CHECK(deposit_paise>=0),
  UNIQUE(room_id,label));
CREATE TABLE IF NOT EXISTS staff_contacts(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL REFERENCES pgs(id),
  role TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'hi', active INTEGER DEFAULT 1);
"""
PG_FIELDS = {"address","phone","basic_rules","food_available","meal_info","rent_rules","guest_rules","guest_max_nights","rent_due_day"}
STAFF_ROLES = {"cook","cleaner","maintenance","electrician","plumber","other"}

class SetupError(Exception): pass

def connect(path=":memory:"):
    c = _connect(path); c.executescript(SCHEMA); return c

def paise(rupees):
    p = round(float(rupees) * 100)
    if p < 0: raise SetupError("Amount cannot be negative")
    return p

def update_pg_details(c, s, **f):
    require_owner(s)
    bad = set(f) - PG_FIELDS
    if bad: raise SetupError(f"Unknown fields: {sorted(bad)}")
    if f.get("rent_due_day") is not None and not 1 <= int(f["rent_due_day"]) <= 28:
        raise SetupError("Rent due day must be 1-28")   # avoids 29/30/31 month-end problems
    if f.get("guest_max_nights") is not None and int(f["guest_max_nights"]) < 0:
        raise SetupError("Guest nights cannot be negative")
    c.execute("INSERT OR IGNORE INTO pg_settings(pg_id) VALUES(?)", (s.pg_id,))
    old = dict(c.execute("SELECT * FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone())
    for k, v in f.items(): c.execute(f"UPDATE pg_settings SET {k}=? WHERE pg_id=?", (v, s.pg_id))
    audit(c, s.pg_id, s.user_id, "pg.details_updated", "pg", s.pg_id, {k: [old[k], v] for k, v in f.items()})
    c.commit()

def set_floors(c, s, count):
    """Floors are numbered from 0 (Ground). Only ever adds floors, never deletes."""
    require_owner(s)
    if not 1 <= int(count) <= 30: raise SetupError("Floors must be 1-30")
    for n in range(int(count)):
        c.execute("INSERT OR IGNORE INTO floors(pg_id,number) VALUES(?,?)", (s.pg_id, n))
    audit(c, s.pg_id, s.user_id, "floors.set", "pg", s.pg_id, {"count": count}); c.commit()

def add_room(c, s, floor_number, room_number, capacity, rent, deposit, room_type="Standard"):
    """rent/deposit are PER BED (decision: a double room's rent is per person). Creates the beds."""
    require_owner(s)
    fl = c.execute("SELECT id FROM floors WHERE pg_id=? AND number=?", (s.pg_id, floor_number)).fetchone()
    if not fl: raise SetupError("That floor does not exist. Set the number of floors first.")
    if c.execute("SELECT 1 FROM rooms WHERE pg_id=? AND room_number=?", (s.pg_id, str(room_number))).fetchone():
        raise SetupError(f"Room {room_number} already exists")
    r, d = paise(rent), paise(deposit)
    rid = c.execute("INSERT INTO rooms(pg_id,floor_id,room_number,room_type,capacity,rent_paise,deposit_paise) VALUES(?,?,?,?,?,?,?)",
                    (s.pg_id, fl["id"], str(room_number), room_type, capacity, r, d)).lastrowid
    for label in "AB"[:capacity]:
        c.execute("INSERT INTO beds(pg_id,room_id,label,rent_paise,deposit_paise) VALUES(?,?,?,?,?)", (s.pg_id, rid, label, r, d))
    audit(c, s.pg_id, s.user_id, "room.created", "room", rid, {"room": room_number, "capacity": capacity, "rent_paise": r, "deposit_paise": d})
    c.commit(); return rid

def update_bed_rent(c, s, bed_id, rent, deposit=None):
    """Money-affecting: logged with old and new values. NOTE: once tenants exist, changing an
    OCCUPIED bed will require explicit owner confirmation (added in the tenants module)."""
    require_owner(s)
    b = c.execute("SELECT * FROM beds WHERE id=? AND pg_id=?", (bed_id, s.pg_id)).fetchone()
    if not b: raise AuthError("Not found")
    new_r = paise(rent); new_d = paise(deposit) if deposit is not None else b["deposit_paise"]
    c.execute("UPDATE beds SET rent_paise=?, deposit_paise=? WHERE id=?", (new_r, new_d, bed_id))
    audit(c, s.pg_id, s.user_id, "bed.rent_changed", "bed", bed_id,
          {"rent_paise": [b["rent_paise"], new_r], "deposit_paise": [b["deposit_paise"], new_d]}); c.commit()

def add_staff(c, s, role, name, phone, language="hi"):
    require_owner(s)
    if role not in STAFF_ROLES: raise SetupError(f"Role must be one of {sorted(STAFF_ROLES)}")
    if language not in ("hi", "en"): raise SetupError("Language must be hi or en")
    sid = c.execute("INSERT INTO staff_contacts(pg_id,role,name,phone,language) VALUES(?,?,?,?,?)",
                    (s.pg_id, role, name, normalize_phone(phone), language)).lastrowid
    audit(c, s.pg_id, s.user_id, "staff.added", "staff", sid, {"role": role, "name": name}); c.commit(); return sid

def set_backup_contact(c, s, name, phone):
    require_owner(s)
    c.execute("INSERT OR IGNORE INTO pg_settings(pg_id) VALUES(?)", (s.pg_id,))
    c.execute("UPDATE pg_settings SET backup_name=?, backup_phone=? WHERE pg_id=?", (name, normalize_phone(phone), s.pg_id))
    audit(c, s.pg_id, s.user_id, "backup_contact.set", "pg", s.pg_id, {"name": name}); c.commit()

def setup_status(c, s):
    """Guided-setup checklist. Rules left blank stay blank - the system never invents them."""
    require_owner(s)
    st = c.execute("SELECT * FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone()
    cnt = lambda t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE pg_id=?", (s.pg_id,)).fetchone()[0]
    todo = []
    if not (st and st["address"] and st["phone"]): todo.append("PG address and contact number")
    if not cnt("floors"): todo.append("Number of floors")
    if not cnt("rooms"): todo.append("Rooms and beds")
    if not (st and st["backup_phone"]): todo.append("Backup contact for escalations")
    if not (st and st["rent_due_day"]): todo.append("Rent due day")
    if not (st and st["guest_max_nights"] is not None): todo.append("Guest rule (max nights) - until set, every guest stay needs your approval")
    return {"complete": not todo, "todo": todo}
