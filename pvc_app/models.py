import json
from datetime import datetime
from flask_login import UserMixin
from . import db, login_manager


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(200))
    email = db.Column(db.String(200), unique=True)
    contact_no = db.Column(db.String(20))
    designation = db.Column(db.String(120))
    control_no = db.Column(db.String(120))
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    code = db.Column(db.String(50), unique=True)
    description = db.Column(db.Text)
    pvc_formula_code = db.Column(db.String(50), nullable=False)
    weights_json = db.Column(db.Text, default="{}")
    extra_fields_json = db.Column(db.Text, default="[]")

    @property
    def weights(self):
        return json.loads(self.weights_json or "{}")

    @property
    def extra_fields(self):
        return json.loads(self.extra_fields_json or "[]")


class ItemIndex(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item = db.relationship("Item")
    month = db.Column(db.Date, nullable=False)
    indices_json = db.Column(db.Text, nullable=False)


class PVCResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User")
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item = db.relationship("Item")
    username = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    basicrate = db.Column(db.Float)
    quantity = db.Column(db.Float)
    freightrateperunit = db.Column(db.Float)
    pvcbasedate = db.Column(db.String(10))
    origdp = db.Column(db.String(10))
    refixeddp = db.Column(db.String(10))
    extendeddp = db.Column(db.String(10))
    caldate = db.Column(db.String(10))
    supdate = db.Column(db.String(10))
    rateapplied = db.Column(db.String(100))
    pvcactual = db.Column(db.Float)
    pvccontractual = db.Column(db.Float)
    loweractual = db.Column(db.Float)
    lowercontractual = db.Column(db.Float)
    ldamtactual = db.Column(db.Float)
    ldamtcontractual = db.Column(db.Float)
    fairprice = db.Column(db.Float)
    selectedscenario = db.Column(db.String(10))
    pvcactuallessldnew = db.Column(db.Float)
    pvccontractuallessldnew = db.Column(db.Float)
    loweractuallessld = db.Column(db.Float)
    lowercontractuallessld = db.Column(db.Float)
    delaydays = db.Column(db.Integer)
    ldweeksnew = db.Column(db.Integer)
    ldratepctnew = db.Column(db.Float)
    ldapplicable = db.Column(db.Boolean, default=False)
    pvcperseta2 = db.Column(db.Float)
    pvcpersetb2 = db.Column(db.Float)
    pvcpersetc1 = db.Column(db.Float)
    pvcpersetd1 = db.Column(db.Float)
    tenderno = db.Column(db.String(100))
    pono = db.Column(db.String(100))
    scenarioamounts_json = db.Column(db.Text)
    scenariodetails_json = db.Column(db.Text)
    igbt_vendor_details_json = db.Column(db.Text)


class TenderMaster(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item = db.relationship("Item")
    tender_no = db.Column(db.String(100), unique=True, nullable=False)
    basicrate = db.Column(db.Float, default=0)
    pvcbasedate = db.Column(db.String(10))
    freightrateperunit = db.Column(db.Float)
    lowerrate = db.Column(db.Float, default=0)
    lowerratebasedate = db.Column(db.String(10))
    lowerfreight = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TenderVendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tender_id = db.Column(db.Integer, db.ForeignKey("tender_master.id"), nullable=False)
    tender = db.relationship("TenderMaster", backref="vendors")
    po_no = db.Column(db.String(50))
    vendor_name = db.Column(db.String(200), nullable=False)
    cif = db.Column(db.Float, default=0)
    currency = db.Column(db.String(10), nullable=False)