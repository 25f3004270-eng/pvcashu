from pvc_app import create_app, db
from pvc_app.models import User, Item, ItemIndex, PVCResult, TenderMaster, TenderVendor

app = create_app("dev")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)