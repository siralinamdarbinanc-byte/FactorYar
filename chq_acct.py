# -*- coding: utf-8 -*-
"""حسابداری چک (فاکتور یار): هر چکِ ثبت‌شده برای مشتری از حساب او کم می‌شود.
قاعده: برای هر چک، تا وقتی وضعیتش «برگشتی» یا «مستردشده» نیست، یک «دریافت» (روش: چک) به همان مبلغ وصل است؛
با برگشتی/مستردشده شدن، آن دریافت حذف می‌شود (با نسخه در سطل بازیافت) و بدهی مشتری دوباره برمی‌گردد.
بدون وابستگی به Qt تا مستقل تست شود."""
import safety

VOID = ("برگشتی", "مستردشده")


def sync(con, user, chq_id, today_iso):
    """دریافتِ وابسته به چک را با وضعیت فعلی چک هماهنگ می‌کند. یکی از 'created' / 'updated' / 'removed' / '' برمی‌گرداند."""
    ch = con.execute("SELECT * FROM cheques WHERE id=?", (chq_id,)).fetchone()
    if not ch:
        return ""
    pays = con.execute("SELECT id FROM payments WHERE cheque_id=?", (chq_id,)).fetchall()
    if ch["status"] in VOID:
        for p in pays:
            safety.archive_delete(con, user, "payment", p["id"], f"چک {ch['status']} شد")
        if pays:
            con.execute("DELETE FROM payments WHERE cheque_id=?", (chq_id,))
            return "removed"
        return ""
    if not pays:
        con.execute("INSERT INTO payments(customer_id,invoice_id,payment_date,amount,method,reference,notes,cheque_id) VALUES(?,?,?,?,?,?,?,?)",
                    (ch["customer_id"], ch["invoice_id"], today_iso, ch["amount"], "چک", ch["cheque_no"] or "", "ثبت چک", chq_id))
        return "created"
    con.execute("UPDATE payments SET customer_id=?, invoice_id=?, amount=?, reference=? WHERE cheque_id=?",
                (ch["customer_id"], ch["invoice_id"], ch["amount"], ch["cheque_no"] or "", chq_id))
    return "updated"


def reconcile(con, user, today_iso):
    """هنگام باز شدن برنامه: چک‌های ثبت‌شده‌ی قبلی را که هنوز از حساب مشتری کم نشده‌اند خودکار اعمال می‌کند.
    اگر برای همان چک قبلاً دستی «دریافت» (روش چک، همان مشتری و مبلغ) ثبت شده باشد، همان دریافت به چک وصل می‌شود تا دو بار کم نشود.
    (وصل‌شده، ساخته‌شده، جمع مبلغ ساخته‌شده) را برمی‌گرداند. چند بار اجرا شود، اثری ندارد."""
    ph = ",".join("?" * len(VOID))
    rows = con.execute(f"SELECT * FROM cheques WHERE status NOT IN ({ph}) AND id NOT IN (SELECT cheque_id FROM payments WHERE cheque_id IS NOT NULL) ORDER BY id", VOID).fetchall()
    linked = created = 0; total = 0.0
    for ch in rows:
        m = con.execute("""SELECT id FROM payments WHERE cheque_id IS NULL AND customer_id=? AND ABS(amount-?)<0.5
                           AND (method='چک' OR (?<>'' AND reference=?)) ORDER BY id LIMIT 1""",
                        (ch["customer_id"], float(ch["amount"]), ch["cheque_no"] or "", ch["cheque_no"] or "")).fetchone()
        if m:
            con.execute("UPDATE payments SET cheque_id=? WHERE id=?", (ch["id"], m["id"])); linked += 1
        elif sync(con, user, ch["id"], today_iso) == "created":
            created += 1; total += float(ch["amount"])
    return linked, created, total


def pending_old(con):
    """چک‌های قبلیِ بی‌دریافت که هنوز از حساب مشتری کم نشده‌اند (برای اعمال یک‌باره)."""
    ph = ",".join("?" * len(VOID))
    return con.execute(f"SELECT id, amount FROM cheques WHERE status NOT IN ({ph}) AND id NOT IN (SELECT cheque_id FROM payments WHERE cheque_id IS NOT NULL)", VOID).fetchall()
