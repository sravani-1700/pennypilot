
import os
import csv
import io
import calendar
from pathlib import Path
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    flash,
    make_response,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "development-key-change-before-deployment",
)

# Use PostgreSQL when DATABASE_URL is configured on Render.
# Otherwise, keep using the existing local SQLite database.
database_url = os.environ.get("DATABASE_URL")

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + str(Path(app.root_path) / "pennypilot.db")
    )

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# ---------------- DATABASE ----------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(10), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    note = db.Column(db.String(200), default="")
    transaction_date = db.Column(
        db.Date,
        nullable=False,
        default=date.today,
    )


class MonthlyIncome(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True,
    )
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    source = db.Column(db.String(50), nullable=False, default="Salary")
    note = db.Column(db.String(200), default="")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "month",
            "year",
            "source",
            name="unique_monthly_income",
        ),
    )


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


# ---------------- HELPERS ----------------

def money(value):
    return Decimal(str(value or 0))


def valid_amount(raw):
    try:
        amount = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None

    if (
        not amount.is_finite()
        or amount <= 0
        or amount > Decimal("9999999999.99")
    ):
        return None

    return amount.quantize(Decimal("0.01"))


def dashboard_url(month=None, year=None):
    params = {}

    if month is not None:
        params["month"] = month

    if year is not None:
        params["year"] = year

    return url_for("dashboard", **params)


def safe_period(month_value, year_value):
    today = date.today()

    try:
        month = int(month_value)
        year = int(year_value)

        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return month, year

    except (TypeError, ValueError):
        pass

    return today.month, today.year


# ---------------- HOME ----------------

@app.route("/")
def home():
    return render_template("index.html")


# ---------------- REGISTER ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("Please fill in every field.", "error")

        elif len(username) > 40:
            flash("Username must be 40 characters or fewer.", "error")

        elif len(email) > 120 or "@" not in email:
            flash("Please enter a valid email address.", "error")

        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")

        elif User.query.filter_by(username=username).first():
            flash("That username is already taken.", "error")

        elif User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")

        else:
            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
            )

            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash("Your PennyPilot account is ready! 🌷", "success")

            return redirect(url_for("dashboard"))

    return render_template("signup.html")


# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash("Welcome back to PennyPilot! 💜", "success")
            return redirect(url_for("dashboard"))

        flash("Email or password is incorrect.", "error")

    return render_template("login.html")


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
@login_required
def dashboard():
    today = date.today()

    selected_month, selected_year = safe_period(
        request.args.get("month", today.month),
        request.args.get("year", today.year),
    )

    transactions = (
        Transaction.query
        .filter_by(user_id=current_user.id)
        .order_by(
            Transaction.transaction_date.desc(),
            Transaction.id.desc(),
        )
        .all()
    )

    old_income = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter_by(user_id=current_user.id, kind="income")
        .scalar()
    )

    all_monthly_income = (
        db.session.query(func.coalesce(func.sum(MonthlyIncome.amount), 0))
        .filter_by(user_id=current_user.id)
        .scalar()
    )

    expense_total = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter_by(user_id=current_user.id, kind="expense")
        .scalar()
    )

    income_total = money(old_income) + money(all_monthly_income)
    expense_total = money(expense_total)

    selected_incomes = (
        MonthlyIncome.query
        .filter_by(
            user_id=current_user.id,
            month=selected_month,
            year=selected_year,
        )
        .order_by(MonthlyIncome.source.asc())
        .all()
    )

    selected_income_total = sum(
        (money(item.amount) for item in selected_incomes),
        Decimal("0.00"),
    )

    month_expenses = [
        item for item in transactions
        if item.kind == "expense"
        and item.transaction_date.month == selected_month
        and item.transaction_date.year == selected_year
    ]

    category_totals = {}

    for item in month_expenses:
        category_totals[item.category] = (
            category_totals.get(item.category, Decimal("0.00"))
            + money(item.amount)
        )

    monthly_expense_total = sum(
        (money(item.amount) for item in month_expenses),
        Decimal("0.00"),
    )

    suggestions = []

    if not transactions and not all_monthly_income:
        suggestions.append(
            "Start by recording your income and first few expenses. "
            "Your personalised insights will appear here."
        )
    else:
        if category_totals:
            biggest = max(category_totals, key=category_totals.get)

            suggestions.append(
                f"{biggest} is your largest expense category this month "
                f"(₹{category_totals[biggest]:.2f}). "
                "Review it for possible savings."
            )
        else:
            suggestions.append(
                "No expenses are recorded for this selected month."
            )

        if selected_income_total > 0:
            remaining = selected_income_total - monthly_expense_total

            if remaining < 0:
                suggestions.append(
                    "Your recorded expenses exceed this month's saved income. "
                    "Review non-essential spending."
                )
            else:
                suggestions.append(
                    f"Your saved income minus expenses is ₹{remaining:.2f}. "
                    "Consider setting a savings goal."
                )
        else:
            suggestions.append(
                "Add your income for this month to compare it with expenses."
            )

    return render_template(
        "dashboard.html",
        user=current_user,
        transactions=transactions,
        income_total=income_total,
        expense_total=expense_total,
        balance=income_total - expense_total,
        selected_month=selected_month,
        selected_year=selected_year,
        selected_month_name=calendar.month_name[selected_month],
        selected_month_incomes=selected_incomes,
        selected_month_income_total=selected_income_total,
        current_year=today.year,
        chart_labels=list(category_totals.keys()),
        chart_values=[float(v) for v in category_totals.values()],
        monthly_expense_total=monthly_expense_total,
        suggestions=suggestions,
        today=today,
    )


# ---------------- ADD MONTHLY INCOME ----------------

@app.route("/add-income", methods=["POST"])
@login_required
def add_income():
    source = request.form.get("source", "Salary").strip()
    note = request.form.get("note", "").strip()
    amount = valid_amount(request.form.get("amount", ""))

    month, year = safe_period(
        request.form.get("month", date.today().month),
        request.form.get("year", date.today().year),
    )

    if amount is None:
        flash("Enter a valid income amount greater than zero.", "error")

    elif not source or len(source) > 50 or len(note) > 200:
        flash("Check the income source and note.", "error")

    else:
        record = MonthlyIncome.query.filter_by(
            user_id=current_user.id,
            month=month,
            year=year,
            source=source,
        ).first()

        if record:
            record.amount = amount
            record.note = note
        else:
            db.session.add(
                MonthlyIncome(
                    user_id=current_user.id,
                    month=month,
                    year=year,
                    amount=amount,
                    source=source,
                    note=note,
                )
            )

        db.session.commit()
        flash("Income saved successfully! 💜", "success")

    return redirect(dashboard_url(month, year))


# ---------------- ADD EXPENSE ----------------

@app.route("/add-transaction", methods=["POST"])
@login_required
def add_transaction():
    category = request.form.get("category", "").strip()
    note = request.form.get("note", "").strip()
    amount = valid_amount(request.form.get("amount", ""))

    try:
        transaction_date = date.fromisoformat(
            request.form.get("date", "")
        )
    except (TypeError, ValueError):
        transaction_date = None

    if amount is None:
        flash("Enter a valid expense amount greater than zero.", "error")

    elif transaction_date is None:
        flash("Enter a valid expense date.", "error")

    elif not category or len(category) > 50 or len(note) > 200:
        flash("Check the category and note.", "error")

    else:
        db.session.add(
            Transaction(
                user_id=current_user.id,
                kind="expense",
                amount=amount,
                category=category,
                note=note,
                transaction_date=transaction_date,
            )
        )

        db.session.commit()
        flash("Expense added successfully! ✨", "success")

    return redirect(url_for("dashboard"))


# ---------------- EDIT EXPENSE ----------------

@app.route(
    "/edit-transaction/<int:transaction_id>",
    methods=["GET", "POST"],
)
@login_required
def edit_transaction(transaction_id):
    item = Transaction.query.filter_by(
        id=transaction_id,
        user_id=current_user.id,
        kind="expense",
    ).first_or_404()

    if request.method == "POST":
        amount = valid_amount(request.form.get("amount", ""))
        category = request.form.get("category", "").strip()
        note = request.form.get("note", "").strip()

        try:
            transaction_date = date.fromisoformat(
                request.form.get("transaction_date", "")
            )
        except (TypeError, ValueError):
            transaction_date = None

        if amount is None:
            flash("Enter a valid amount greater than zero.", "error")

        elif transaction_date is None:
            flash("Enter a valid date.", "error")

        elif not category or len(category) > 50 or len(note) > 200:
            flash("Check the category and note.", "error")

        else:
            item.amount = amount
            item.category = category
            item.note = note
            item.transaction_date = transaction_date

            db.session.commit()
            flash("Expense updated successfully! 💜", "success")

            return redirect(
                dashboard_url(
                    transaction_date.month,
                    transaction_date.year,
                )
            )

    return render_template("edit_expense.html", item=item)


# ---------------- DELETE EXPENSE ----------------

@app.route(
    "/delete-transaction/<int:transaction_id>",
    methods=["POST"],
)
@login_required
def delete_transaction(transaction_id):
    item = Transaction.query.filter_by(
        id=transaction_id,
        user_id=current_user.id,
        kind="expense",
    ).first_or_404()

    month = item.transaction_date.month
    year = item.transaction_date.year

    db.session.delete(item)
    db.session.commit()

    flash("Expense deleted.", "success")
    return redirect(dashboard_url(month, year))


# ---------------- DELETE INCOME ----------------

@app.route("/delete-income/<int:income_id>", methods=["POST"])
@login_required
def delete_income(income_id):
    item = MonthlyIncome.query.filter_by(
        id=income_id,
        user_id=current_user.id,
    ).first_or_404()

    month, year = item.month, item.year

    db.session.delete(item)
    db.session.commit()

    flash("Income record deleted.", "success")
    return redirect(dashboard_url(month, year))


# ---------------- CSV EXPORT ----------------

@app.route("/export-csv")
@login_required
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        ["Date", "Type", "Category / Source", "Amount (INR)", "Note"]
    )

    items = (
        Transaction.query
        .filter_by(user_id=current_user.id)
        .order_by(Transaction.transaction_date.asc())
        .all()
    )

    for item in items:
        writer.writerow(
            [
                item.transaction_date.isoformat(),
                item.kind,
                item.category,
                str(item.amount),
                item.note or "",
            ]
        )

    incomes = (
        MonthlyIncome.query
        .filter_by(user_id=current_user.id)
        .order_by(MonthlyIncome.year, MonthlyIncome.month)
        .all()
    )

    for item in incomes:
        writer.writerow(
            [
                f"{item.year}-{item.month:02d}",
                "income",
                item.source,
                str(item.amount),
                item.note or "",
            ]
        )

    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        "attachment; filename=pennypilot-transactions.csv"
    )

    return response


# ---------------- LOGOUT ----------------

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have signed out safely.", "success")
    return redirect(url_for("home"))


# ---------------- START ----------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)
