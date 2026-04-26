# pvc_app/admin.py

import json
from datetime import datetime, date

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
)
from flask_login import login_required, current_user

from . import db
from .models import Item, ItemIndex, TenderMaster, TenderVendor  # [file:1]

admin_bp = Blueprint("admin", __name__)


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)

    return wrapper  # [file:1]


# ---------- Items ----------

@admin_bp.route("/items")
@login_required
@admin_required
def items_list():
    items = Item.query.order_by(Item.name.asc()).all()
    return render_template("admin_items_list.html", items=items)  # [file:6]


@admin_bp.route("/items/new", methods=["GET", "POST"])
@login_required
@admin_required
def items_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip()
        formula = (request.form.get("pvcformulacode") or "").strip()
        weightsjson = (request.form.get("weightsjson") or "").strip()
        extrafieldsjson = (request.form.get("extrafieldsjson") or "").strip()
        description = request.form.get("description")

        if not name or not formula:
            flash("Name and formula code are required.", "danger")
            return redirect(url_for("admin.items_new"))

        try:
            json.loads(weightsjson or "{}")
            json.loads(extrafieldsjson or "[]")
        except Exception:
            flash("Weights / extra fields must be valid JSON.", "danger")
            return redirect(url_for("admin.items_new"))

        it = Item(
            name=name,
            code=code or None,
            pvc_formula_code=formula,
            weights_json=weightsjson or "{}",
            extra_fields_json=extrafieldsjson or "[]",
            description=description,
        )
        db.session.add(it)
        db.session.commit()
        flash("Item added successfully.", "success")
        return redirect(url_for("admin.items_list"))

    return render_template("admin_items_form.html", item=None)  # [file:4]


@admin_bp.route("/items/<int:itemid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def items_edit(itemid):
    it = Item.query.get_or_404(itemid)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip()
        formula = (request.form.get("pvcformulacode") or "").strip()
        weightsjson = (request.form.get("weightsjson") or "").strip()
        extrafieldsjson = (request.form.get("extrafieldsjson") or "").strip()
        description = request.form.get("description")

        if not name or not formula:
            flash("Name and formula code are required.", "danger")
            return redirect(url_for("admin.items_edit", itemid=it.id))

        try:
            json.loads(weightsjson or "{}")
            json.loads(extrafieldsjson or "[]")
        except Exception:
            flash("Weights / extra fields must be valid JSON.", "danger")
            return redirect(url_for("admin.items_edit", itemid=it.id))

        it.name = name
        it.code = code or None
        it.pvc_formula_code = formula
        it.weights_json = weightsjson or "{}"
        it.extra_fields_json = extrafieldsjson or "[]"
        it.description = description
        db.session.commit()
        flash("Item updated successfully.", "success")
        return redirect(url_for("admin.items_list"))

    return render_template("admin_items_form.html", item=it)  # [file:4]


# ---------- Item indices ----------

@admin_bp.route("/items/<int:itemid>/indices")
@login_required
@admin_required
def item_indices_list(itemid):
    item = Item.query.get_or_404(itemid)
    rows = (
        ItemIndex.query
        .filter_by(item_id=item.id)
        .order_by(ItemIndex.month.desc())
        .all()
    )
    return render_template("admin_item_indices_list.html", item=item, rows=rows)  # [file:3]


@admin_bp.route("/items/<int:itemid>/indices/new", methods=["GET", "POST"])
@login_required
@admin_required
def item_indices_new(itemid):
    item = Item.query.get_or_404(itemid)
    if request.method == "POST":
        try:
            monthstr = request.form.get("month") or ""
            m = datetime.strptime(monthstr, "%Y-%m-%d").date()
            m = date(m.year, m.month, 1)
        except Exception:
            flash("Invalid month date. Use YYYY-MM-DD format.", "danger")
            return redirect(url_for("admin.item_indices_new", itemid=item.id))

        indicesjson = (request.form.get("indicesjson") or "").strip()
        try:
            json.loads(indicesjson or "{}")
        except Exception:
            flash("Indices must be valid JSON, e.g. {\"C\":100,\"AL\":200}.", "danger")
            return redirect(url_for("admin.item_indices_new", itemid=item.id))

        existing = ItemIndex.query.filter_by(item_id=item.id, month=m).first()
        if existing:
            flash(f"Indices for {m.strftime('%B %Y')} already exist. Edit instead.", "warning")
            return redirect(url_for("admin.item_indices_list", itemid=item.id))

        row = ItemIndex(item_id=item.id, month=m, indices_json=indicesjson or "{}")
        db.session.add(row)
        db.session.commit()
        flash(f"Indices for {m.strftime('%B %Y')} added.", "success")
        return redirect(url_for("admin.item_indices_list", itemid=item.id))

    return render_template("admin_item_indices_form.html", item=item, row=None)  # [file:2]


@admin_bp.route("/items/<int:itemid>/indices/<int:rowid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def item_indices_edit(itemid, rowid):
    item = Item.query.get_or_404(itemid)
    row = ItemIndex.query.filter_by(id=rowid, item_id=item.id).first_or_404()
    if request.method == "POST":
        try:
            monthstr = request.form.get("month") or ""
            m = datetime.strptime(monthstr, "%Y-%m-%d").date()
            row.month = date(m.year, m.month, 1)
        except Exception:
            flash("Invalid month date. Use YYYY-MM-DD format.", "danger")
            return redirect(url_for("admin.item_indices_edit", itemid=item.id, rowid=row.id))

        indicesjson = (request.form.get("indicesjson") or "").strip()
        try:
            json.loads(indicesjson or "{}")
        except Exception:
            flash("Indices must be valid JSON, e.g. {\"C\":100,\"AL\":200}.", "danger")
            return redirect(url_for("admin.item_indices_edit", itemid=item.id, rowid=row.id))

        row.indices_json = indicesjson or "{}"
        db.session.commit()
        flash("Indices updated.", "success")
        return redirect(url_for("admin.item_indices_list", itemid=item.id))

    return render_template("admin_item_indices_form.html", item=item, row=row)  # [file:2]


# ---------- Tenders + vendors ----------

def _safe_float(x):
    try:
        return float(str(x or 0).replace(",", "").strip())
    except Exception:
        return 0.0  # [file:1]


@admin_bp.route("/tenders")
@login_required
@admin_required
def tenders_list():
    tenders = TenderMaster.query.order_by(TenderMaster.created_at.desc()).all()
    return render_template("admin_tenders_list.html", tenders=tenders)  # [file:9]


@admin_bp.route("/tenders/new", methods=["GET", "POST"])
@login_required
@admin_required
def tenders_new():
    items = Item.query.order_by(Item.name.asc()).all()
    if request.method == "POST":
        itemid = request.form.get("itemid")
        tenderno = (request.form.get("tenderno") or "").strip()

        if not itemid or not tenderno:
            flash("Item and Tender No are required.", "danger")
            return redirect(url_for("admin.tenders_new"))

        if TenderMaster.query.filter_by(tender_no=tenderno).first():
            flash("Tender No already exists.", "danger")
            return redirect(url_for("admin.tenders_new"))

        row = TenderMaster(
            item_id=int(itemid),
            tender_no=tenderno,
            basicrate=_safe_float(request.form.get("basicrate")),
            pvcbasedate=request.form.get("pvcbasedate") or "",
            lowerrate=_safe_float(request.form.get("lowerrate")),
            lowerratebasedate=request.form.get("lowerratebasedate") or "",
            freightrateperunit=_safe_float(request.form.get("freightrateperunit")),
            lowerfreight=_safe_float(request.form.get("lowerfreight")),
        )
        db.session.add(row)
        db.session.commit()
        flash("Tender added successfully.", "success")
        return redirect(url_for("admin.tenders_list"))

    return render_template("admin_tenders_form.html", tender=None, items=items)  # [file:7]


@admin_bp.route("/tenders/<int:tenderid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def tenders_edit(tenderid):
    tender = TenderMaster.query.get_or_404(tenderid)
    items = Item.query.order_by(Item.name.asc()).all()
    if request.method == "POST":
        itemid = request.form.get("itemid")
        tenderno = (request.form.get("tenderno") or "").strip()
        if not itemid or not tenderno:
            flash("Item and Tender No are required.", "danger")
            return redirect(url_for("admin.tenders_edit", tenderid=tender.id))

        tender.item_id = int(itemid)
        tender.tender_no = tenderno
        tender.basicrate = _safe_float(request.form.get("basicrate"))
        tender.pvcbasedate = request.form.get("pvcbasedate") or ""
        tender.lowerrate = _safe_float(request.form.get("lowerrate"))
        tender.lowerratebasedate = request.form.get("lowerratebasedate") or ""
        tender.freightrateperunit = _safe_float(request.form.get("freightrateperunit"))
        tender.lowerfreight = _safe_float(request.form.get("lowerfreight"))
        db.session.commit()
        flash("Tender updated successfully.", "success")
        return redirect(url_for("admin.tenders_list"))

    return render_template("admin_tenders_form.html", tender=tender, items=items)  # [file:7]


@admin_bp.route("/tenders/<int:tenderid>/vendors")
@login_required
@admin_required
def tender_vendors_list(tenderid):
    tender = TenderMaster.query.get_or_404(tenderid)
    rows = (
        TenderVendor.query
        .filter_by(tender_id=tender.id)
        .order_by(TenderVendor.id.asc())
        .all()
    )
    return render_template("admin_tender_vendors_list.html", tender=tender, rows=rows)  # [file:8]


@admin_bp.route("/tenders/<int:tenderid>/vendors/new", methods=["GET", "POST"])
@login_required
@admin_required
def tender_vendors_new(tenderid):
    tender = TenderMaster.query.get_or_404(tenderid)
    if request.method == "POST":
        vendor_name = (request.form.get("vendorname") or "").strip()
        po_no = (request.form.get("pono") or "").strip()
        cif = _safe_float(request.form.get("cif"))
        currency = (request.form.get("currency") or "").strip()
        if not vendor_name or not currency:
            flash("Vendor name and currency are required.", "danger")
            return redirect(url_for("admin.tender_vendors_new", tenderid=tender.id))

        row = TenderVendor(
            tender_id=tender.id,
            vendor_name=vendor_name,
            po_no=po_no or None,
            cif=cif,
            currency=currency,
        )
        db.session.add(row)
        db.session.commit()
        flash("Vendor added.", "success")
        return redirect(url_for("admin.tender_vendors_list", tenderid=tender.id))

    return render_template("admin_tender_vendor_form.html", tender=tender, row=None)  # [file:5]

# ── Delete Item ──────────────────────────────────────────────────────────────
@admin_bp.route('/items/<int:itemid>/delete', methods=['POST'])
@login_required
@admin_required
def items_delete(itemid):
    it = Item.query.get_or_404(itemid)
    db.session.delete(it)
    db.session.commit()
    flash('Item deleted.', 'success')
    return redirect(url_for('admin.items_list'))


# ── Delete Item Index Row ─────────────────────────────────────────────────────
@admin_bp.route('/items/<int:itemid>/indices/<int:rowid>/delete', methods=['POST'])
@login_required
@admin_required
def item_indices_delete(itemid, rowid):
    row = ItemIndex.query.filter_by(id=rowid, item_id=itemid).first_or_404()
    db.session.delete(row)
    db.session.commit()
    flash('Index row deleted.', 'success')
    return redirect(url_for('admin.item_indices_list', itemid=itemid))


# ── Delete Tender ─────────────────────────────────────────────────────────────
@admin_bp.route('/tenders/<int:tenderid>/delete', methods=['POST'])
@login_required
@admin_required
def tenders_delete(tenderid):
    tender = TenderMaster.query.get_or_404(tenderid)
    # Also delete all associated vendors
    TenderVendor.query.filter_by(tender_id=tender.id).delete()
    db.session.delete(tender)
    db.session.commit()
    flash('Tender and its vendors deleted.', 'success')
    return redirect(url_for('admin.tenders_list'))


# ── Delete Tender Vendor ──────────────────────────────────────────────────────
@admin_bp.route('/tenders/<int:tenderid>/vendors/<int:rowid>/delete', methods=['POST'])
@login_required
@admin_required
def tender_vendors_delete(tenderid, rowid):
    row = TenderVendor.query.filter_by(tender_id=tenderid, id=rowid).first_or_404()
    db.session.delete(row)
    db.session.commit()
    flash('Vendor deleted.', 'success')
    return redirect(url_for('admin.tender_vendors_list', tenderid=tenderid))
@admin_bp.route("/tenders/<int:tenderid>/vendors/<int:rowid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def tender_vendors_edit(tenderid, rowid):
    tender = TenderMaster.query.get_or_404(tenderid)
    row = TenderVendor.query.filter_by(tender_id=tender.id, id=rowid).first_or_404()
    if request.method == "POST":
        vendor_name = (request.form.get("vendorname") or "").strip()
        po_no = (request.form.get("pono") or "").strip()
        cif = _safe_float(request.form.get("cif"))
        currency = (request.form.get("currency") or "").strip()
        if not vendor_name or not currency:
            flash("Vendor name and currency are required.", "danger")
            return redirect(url_for("admin.tender_vendors_edit", tenderid=tender.id, rowid=row.id))

        row.vendor_name = vendor_name
        row.po_no = po_no or None
        row.cif = cif
        row.currency = currency
        db.session.commit()
        flash("Vendor updated.", "success")
        return redirect(url_for("admin.tender_vendors_list", tenderid=tender.id))

    return render_template("admin_tender_vendor_form.html", tender=tender, row=row)  # [file:5]