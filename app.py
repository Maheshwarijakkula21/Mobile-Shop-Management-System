from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Protocol, cast, overload
from uuid import uuid4

from flask import Flask, jsonify, request, session, send_from_directory
import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract
from mysql.connector import Error
from mysql.connector.pooling import PooledMySQLConnection
from mysql.connector.types import MySQLConvertibleType
from werkzeug.security import check_password_hash, generate_password_hash


# ============================================================
# MOBILE SHOP MANAGEMENT SYSTEM
# Two-file architecture:
#   app.py    -> Flask backend + MySQL + all APIs
#   index.html -> HTML + CSS + JavaScript frontend
# ============================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MOBILE_SHOP_SECRET", "change-this-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# ------------------------------------------------------------
# DATABASE CONFIGURATION
# Replace these values with your local MySQL details later.
# ------------------------------------------------------------
DB_HOST = os.environ.get("MYSQL_HOST", "localhost")
DB_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DB_USER = os.environ.get("MYSQL_USER", "root")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD", "12345")
DB_NAME = os.environ.get("MYSQL_DATABASE", "mobile_shop")


class DictionaryCursor(Protocol):
    @property
    def lastrowid(self) -> int | None: ...

    @property
    def rowcount(self) -> int: ...

    def execute(
        self,
        operation: str,
        params: Sequence[MySQLConvertibleType] = (),
        map_results: bool = False,
    ) -> None: ...

    def executemany(
        self,
        operation: str,
        seq_params: Sequence[Sequence[MySQLConvertibleType]],
    ) -> None: ...

    def fetchone(self) -> dict[str, Any] | None: ...

    def fetchall(self) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def get_dictionary_cursor(
    connection: MySQLConnectionAbstract | PooledMySQLConnection,
) -> DictionaryCursor:
    return cast(DictionaryCursor, connection.cursor(dictionary=True))


# ============================================================
# COMMON RESPONSE HELPERS
# ============================================================

def ok(data: Any = None, message: str = "Success", status: int = 200):
    payload = {"success": True, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status


def fail(message: str, status: int = 400, details: Any = None):
    payload = {"success": False, "message": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def serialize_value(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


@overload
def serialize_row(row: dict[str, Any]) -> dict[str, Any]: ...


@overload
def serialize_row(row: None) -> None: ...


def serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: serialize_value(v) for k, v in row.items()}


def serialize_rows(rows: list[dict[str, Any]]):
    return [serialize_row(row) for row in rows]


# ============================================================
# MYSQL CONNECTION HELPERS
# ============================================================

def get_db_connection(database: bool = True):
    config = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
    }
    if database:
        config["database"] = DB_NAME
    return mysql.connector.connect(**config)


def ensure_database():
    """Create the database if it does not already exist."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection(database=False)
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        connection.commit()
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def execute_query(
    query: str,
    params: Sequence[MySQLConvertibleType] | Sequence[Sequence[MySQLConvertibleType]] | None = None,
    fetch: bool = False,
    many: bool = False,
    commit: bool = True,
) -> tuple[list[dict[str, Any]], int | None, int]:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        if many:
            cursor.executemany(
                query,
                cast(Sequence[Sequence[MySQLConvertibleType]], params or ()),
            )
        else:
            cursor.execute(query, cast(Sequence[MySQLConvertibleType], params or ()))
        rows = cursor.fetchall() if fetch else []
        last_id = cursor.lastrowid
        affected = cursor.rowcount
        if commit:
            connection.commit()
        return rows, last_id, affected
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def table_columns(table: str):
    rows, _, _ = execute_query(f"SHOW COLUMNS FROM `{table}`", fetch=True)
    return [r["Field"] for r in rows]


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_database():
    """Create all required tables and seed starter data."""
    ensure_database()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)

        statements = [
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                phone VARCHAR(20) NOT NULL UNIQUE,
                email VARCHAR(160) NOT NULL UNIQUE,
                city VARCHAR(80) DEFAULT '',
                address VARCHAR(255) DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_customer_name (name),
                INDEX idx_customer_email (email)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(80) NOT NULL UNIQUE,
                email VARCHAR(160) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role ENUM('customer','admin') NOT NULL,
                customer_id INT NULL,
                active TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE SET NULL,
                INDEX idx_users_role (role)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS brands (
                brand_id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_brand_name (name)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS suppliers (
                supplier_id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                phone VARCHAR(20) NOT NULL UNIQUE,
                email VARCHAR(160) NOT NULL UNIQUE,
                address VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_supplier_name (name)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id INT AUTO_INCREMENT PRIMARY KEY,
                brand_id INT NOT NULL,
                model VARCHAR(140) NOT NULL,
                ram VARCHAR(30) NOT NULL,
                storage VARCHAR(30) NOT NULL,
                color VARCHAR(50) NOT NULL,
                price DECIMAL(12,2) NOT NULL,
                stock INT NOT NULL DEFAULT 0,
                description TEXT,
                supplier_id INT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (brand_id) REFERENCES brands(brand_id) ON DELETE RESTRICT,
                FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE SET NULL,
                INDEX idx_product_model (model),
                INDEX idx_product_brand (brand_id),
                INDEX idx_product_stock (stock)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS sales (
                sale_id INT AUTO_INCREMENT PRIMARY KEY,
                order_number VARCHAR(30) NOT NULL UNIQUE,
                customer_id INT NOT NULL,
                sale_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                payment_method ENUM('Cash','Card','UPI') NOT NULL DEFAULT 'UPI',
                status ENUM('Placed','Processing','Completed','Cancelled') NOT NULL DEFAULT 'Placed',
                notes VARCHAR(500) DEFAULT '',
                created_by INT NULL,
                cancelled_at DATETIME NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE RESTRICT,
                FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL,
                INDEX idx_sales_customer (customer_id),
                INDEX idx_sales_date (sale_date),
                INDEX idx_sales_status (status)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS sale_items (
                sale_item_id INT AUTO_INCREMENT PRIMARY KEY,
                sale_id INT NOT NULL,
                product_id INT NOT NULL,
                quantity INT NOT NULL,
                unit_price DECIMAL(12,2) NOT NULL,
                subtotal DECIMAL(12,2) NOT NULL,
                FOREIGN KEY (sale_id) REFERENCES sales(sale_id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT,
                INDEX idx_sale_items_sale (sale_id),
                INDEX idx_sale_items_product (product_id)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS stock_logs (
                log_id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                change_amount INT NOT NULL,
                reason ENUM('Restock','Sale','Cancellation','Adjustment') NOT NULL DEFAULT 'Restock',
                user_id INT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
                INDEX idx_stock_logs_product (product_id),
                INDEX idx_stock_logs_date (created_at)
            ) ENGINE=InnoDB
            """,
        ]

        for statement in statements:
            cursor.execute(statement)

        connection.commit()

        # Starter brands. Safe to run repeatedly.
        cursor.executemany(
            "INSERT IGNORE INTO brands (name) VALUES (%s)",
            [(name,) for name in [
                "Samsung", "Apple", "OnePlus", "Vivo", "Oppo",
                "Xiaomi", "Realme", "Motorola", "Nothing", "Google"
            ]],
        )

        # Starter suppliers.
        suppliers = [
            ("Tech Distributors India", "9000000001", "sales@techdistributors.example", "Hyderabad, Telangana"),
            ("Mobile World Wholesale", "9000000002", "orders@mobileworld.example", "Secunderabad, Telangana"),
            ("Smart Devices Supply", "9000000003", "support@smartdevices.example", "Bengaluru, Karnataka"),
        ]
        cursor.executemany(
            """
            INSERT IGNORE INTO suppliers (name, phone, email, address)
            VALUES (%s, %s, %s, %s)
            """,
            suppliers,
        )

        # Demo/seed customer, useful immediately after setup.
        cursor.execute("SELECT customer_id FROM customers WHERE email=%s", ("customer@example.com",))
        customer_row = cursor.fetchone()
        if not customer_row:
            cursor.execute(
                """
                INSERT INTO customers (name, phone, email, city, address)
                VALUES (%s, %s, %s, %s, %s)
                """,
                ("Demo Customer", "9000000010", "customer@example.com", "Hyderabad", "Demo Address"),
            )
            demo_customer_id = cursor.lastrowid
        else:
            demo_customer_id = customer_row["customer_id"]

        # Demo admin.
        cursor.execute("SELECT user_id FROM users WHERE username=%s", ("admin",))
        if not cursor.fetchone():
            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash, role)
                VALUES (%s, %s, %s, 'admin')
                """,
                ("admin", "admin@example.com", "admin123"),
            )

        # Demo customer login account.
        cursor.execute("SELECT user_id FROM users WHERE username=%s", ("customer",))
        if not cursor.fetchone():
            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash, role, customer_id)
                VALUES (%s, %s, %s, 'customer', %s)
                """,
                ("customer", "customer@example.com", "customer123", demo_customer_id),
            )

        # Starter products, inserted only when catalog is empty.
        cursor.execute("SELECT COUNT(*) AS product_count FROM products")
        product_count_row = cursor.fetchone()
        if product_count_row and product_count_row["product_count"] == 0:
            cursor.execute("SELECT brand_id, name FROM brands")
            brand_map = {
                row["name"]: row["brand_id"]
                for row in cursor.fetchall()
            }
            cursor.execute("SELECT supplier_id FROM suppliers ORDER BY supplier_id LIMIT 1")
            supplier_row = cursor.fetchone()
            if supplier_row is None:
                raise RuntimeError("Starter supplier data was not created.")
            default_supplier = supplier_row["supplier_id"]
            seed_products = [
                (brand_map["Samsung"], "Galaxy S25", "12 GB", "256 GB", "Navy", 79999, 12, "Flagship Samsung smartphone", default_supplier),
                (brand_map["Apple"], "iPhone 16", "8 GB", "128 GB", "Black", 69999, 8, "Latest iPhone model", default_supplier),
                (brand_map["OnePlus"], "OnePlus 13", "12 GB", "256 GB", "Midnight", 64999, 10, "Performance-focused flagship", default_supplier),
                (brand_map["Vivo"], "Vivo V40", "8 GB", "256 GB", "Silver", 42999, 15, "Camera-focused smartphone", default_supplier),
                (brand_map["Oppo"], "Reno 13", "8 GB", "128 GB", "Purple", 39999, 9, "Slim smartphone with premium design", default_supplier),
                (brand_map["Xiaomi"], "Redmi Note 14 Pro", "8 GB", "256 GB", "Black", 29999, 20, "Value flagship smartphone", default_supplier),
                (brand_map["Realme"], "GT 7", "12 GB", "256 GB", "Blue", 37999, 11, "High-performance Android phone", default_supplier),
                (brand_map["Motorola"], "Edge 60", "8 GB", "256 GB", "Green", 34999, 6, "Clean Android experience", default_supplier),
                (brand_map["Nothing"], "Phone 3", "12 GB", "256 GB", "White", 49999, 4, "Distinctive transparent design", default_supplier),
                (brand_map["Google"], "Pixel 10", "12 GB", "256 GB", "Obsidian", 74999, 5, "Google AI and camera experience", default_supplier),
            ]
            cursor.executemany(
                """
                INSERT INTO products
                    (brand_id, model, ram, storage, color, price, stock, description, supplier_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                seed_products,
            )

        connection.commit()
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ============================================================
# AUTHENTICATION / SESSION
# ============================================================

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    rows, _, _ = execute_query(
        """
        SELECT u.user_id, u.username, u.email, u.role, u.customer_id,
               c.name AS customer_name, c.phone, c.city, c.address
        FROM users u
        LEFT JOIN customers c ON c.customer_id = u.customer_id
        WHERE u.user_id=%s AND u.active=1
        """,
        (user_id,),
        fetch=True,
    )
    return serialize_row(rows[0]) if rows else None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return fail("Please log in first.", 401)
        return view(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return fail("Please log in first.", 401)
            if user["role"] not in roles:
                return fail("You do not have permission to perform this action.", 403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# VALIDATION HELPERS
# ============================================================

def required(data: dict, fields: list[str]):
    missing = [field for field in fields if data.get(field) in (None, "")]
    return missing


def clean_text(value: Any, max_len: int = 255):
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def validate_phone(phone: str):
    return bool(re.fullmatch(r"[0-9+()\-\s]{7,20}", phone))


def parse_money(value: Any):
    try:
        amount = Decimal(str(value))
        if amount <= 0:
            raise ValueError
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_nonnegative_int(value: Any):
    try:
        number = int(value)
        if number < 0:
            raise ValueError
        return number
    except (TypeError, ValueError):
        return None


def product_payload(data: dict):
    missing = required(data, ["brand_id", "model", "ram", "storage", "color", "price", "stock"])
    if missing:
        raise ValueError("Missing fields: " + ", ".join(missing))

    brand_id = parse_nonnegative_int(data["brand_id"])
    stock = parse_nonnegative_int(data["stock"])
    price = parse_money(data["price"])
    if not brand_id:
        raise ValueError("Choose a valid brand.")
    if price is None:
        raise ValueError("Price must be greater than zero.")
    if stock is None:
        raise ValueError("Stock must be zero or greater.")

    return {
        "brand_id": brand_id,
        "model": clean_text(data["model"], 140),
        "ram": clean_text(data["ram"], 30),
        "storage": clean_text(data["storage"], 30),
        "color": clean_text(data["color"], 50),
        "price": price,
        "stock": stock,
        "description": clean_text(data.get("description", ""), 2000),
        "supplier_id": parse_nonnegative_int(data["supplier_id"]) if data.get("supplier_id") else None,
    }


# ============================================================
# FRONTEND ROUTE
# ============================================================

@app.get("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.get("/api/health")
def health():
    try:
        rows, _, _ = execute_query("SELECT DATABASE() AS db, NOW() AS server_time", fetch=True)
        return ok({"database": rows[0]["db"], "server_time": serialize_value(rows[0]["server_time"])}, "Backend and MySQL are connected.")
    except Error as exc:
        return fail(f"Database connection failed: {exc}", 500)


# ============================================================
# AUTH APIs
# ============================================================

@app.get("/api/session")
def session_info():
    user = current_user()
    if not user:
        return ok({"authenticated": False}, "Not logged in.")
    return ok({"authenticated": True, "user": user}, "Session active.")


def verify_user_password(stored_hash: str, provided_password: str) -> bool:
    if not stored_hash or not provided_password:
        return False
    try:
        if check_password_hash(stored_hash, provided_password):
            return True
    except Exception:
        pass
    return stored_hash == provided_password


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    login_value = clean_text(data.get("login"), 160).lower()
    password = str(data.get("password", ""))
    requested_role = clean_text(data.get("role"), 20).lower()

    if not login_value or not password:
        return fail("Username/email and password are required.")
    if requested_role not in {"customer", "admin"}:
        return fail("Invalid login role.")

    rows, _, _ = execute_query(
        """
        SELECT u.user_id, u.username, u.email, u.password_hash, u.role, u.customer_id,
               c.name AS customer_name, c.phone, c.city, c.address
        FROM users u
        LEFT JOIN customers c ON c.customer_id = u.customer_id
        WHERE (LOWER(u.username)=LOWER(%s) OR LOWER(u.email)=LOWER(%s))
          AND u.active=1
        LIMIT 1
        """,
        (login_value, login_value),
        fetch=True,
    )

    if not rows or not verify_user_password(rows[0]["password_hash"], password):
        return fail("Invalid username/email or password.", 401)
    if rows[0]["role"] != requested_role:
        return fail("The selected role does not match this account.", 403)

    session.permanent = True
    session["user_id"] = rows[0]["user_id"]

    user = {k: v for k, v in rows[0].items() if k != "password_hash"}
    return ok({"user": serialize_row(user)}, "Login successful.")


@app.post("/api/logout")
def logout():
    session.clear()
    return ok(message="Logged out successfully.")


@app.post("/api/register")
def register_customer():
    data = request.get_json(silent=True) or {}
    missing = required(data, ["username", "password", "name", "phone", "email"])
    if missing:
        return fail("Missing fields: " + ", ".join(missing))

    username = clean_text(data["username"], 80)
    password = str(data["password"])
    name = clean_text(data["name"], 120)
    phone = clean_text(data["phone"], 20)
    email = clean_text(data["email"], 160).lower()
    city = clean_text(data.get("city", ""), 80)
    address = clean_text(data.get("address", ""), 255)

    if len(username) < 3:
        return fail("Username must contain at least 3 characters.")
    if len(password) < 6:
        return fail("Password must contain at least 6 characters.")
    if not validate_phone(phone):
        return fail("Enter a valid phone number.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return fail("Enter a valid email address.")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        cursor.execute("SELECT user_id FROM users WHERE username=%s OR email=%s", (username, email))
        if cursor.fetchone():
            return fail("Username or email is already registered.", 409)
        cursor.execute("SELECT customer_id FROM customers WHERE phone=%s OR email=%s", (phone, email))
        if cursor.fetchone():
            return fail("A customer with this phone or email already exists.", 409)

        cursor.execute(
            """
            INSERT INTO customers (name, phone, email, city, address)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (name, phone, email, city, address),
        )
        customer_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO users (username, email, password_hash, role, customer_id)
            VALUES (%s, %s, %s, 'customer', %s)
            """,
            (username, email, password, customer_id),
        )
        connection.commit()
        return ok({"customer_id": customer_id}, "Customer account created.", 201)
    except Error as exc:
        if connection:
            connection.rollback()
        if getattr(exc, "errno", None) == 1062:
            return fail("Username, email, or phone number is already registered.", 409)
        return fail(f"Registration failed: {exc}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ============================================================
# BRANDS
# ============================================================

@app.get("/api/brands")
@login_required
def get_brands():
    rows, _, _ = execute_query(
        """
        SELECT b.brand_id AS id, b.name, COUNT(p.product_id) AS product_count, b.created_at
        FROM brands b
        LEFT JOIN products p ON p.brand_id=b.brand_id
        GROUP BY b.brand_id
        ORDER BY b.name
        """,
        fetch=True,
    )
    return ok(serialize_rows(rows))


@app.post("/api/brands")
@role_required("admin")
def create_brand():
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), 100)
    if not name:
        return fail("Brand name is required.")
    try:
        _, brand_id, _ = execute_query("INSERT INTO brands (name) VALUES (%s)", (name,))
        return ok({"id": brand_id, "name": name}, "Brand created.", 201)
    except Error as exc:
        if exc.errno == 1062:
            return fail("Brand already exists.", 409)
        return fail(f"Unable to create brand: {exc}", 500)


@app.put("/api/brands/<int:brand_id>")
@role_required("admin")
def update_brand(brand_id: int):
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), 100)
    if not name:
        return fail("Brand name is required.")
    try:
        exists, _, _ = execute_query("SELECT brand_id FROM brands WHERE brand_id=%s", (brand_id,), fetch=True)
        if not exists:
            return fail("Brand not found.", 404)
        execute_query("UPDATE brands SET name=%s WHERE brand_id=%s", (name, brand_id))
        return ok(message="Brand updated.")
    except Error as exc:
        if exc.errno == 1062:
            return fail("Another brand already uses this name.", 409)
        return fail(f"Unable to update brand: {exc}", 500)


@app.delete("/api/brands/<int:brand_id>")
@role_required("admin")
def delete_brand(brand_id: int):
    rows, _, _ = execute_query("SELECT COUNT(*) AS count FROM products WHERE brand_id=%s", (brand_id,), fetch=True)
    if rows[0]["count"]:
        return fail("This brand is linked to products and cannot be deleted.", 409)
    try:
        _, _, affected = execute_query("DELETE FROM brands WHERE brand_id=%s", (brand_id,))
        if not affected:
            return fail("Brand not found.", 404)
        return ok(message="Brand deleted.")
    except Error as exc:
        return fail(f"Unable to delete brand: {exc}", 500)


# ============================================================
# PRODUCTS
# ============================================================

PRODUCT_SELECT = """
SELECT p.product_id AS id, p.brand_id, b.name AS brand, p.model, p.ram, p.storage,
       p.color, p.price, p.stock, p.description, p.supplier_id,
       s.name AS supplier, p.created_at, p.updated_at
FROM products p
JOIN brands b ON b.brand_id=p.brand_id
LEFT JOIN suppliers s ON s.supplier_id=p.supplier_id
"""


@app.get("/api/products")
@login_required
def get_products():
    search = clean_text(request.args.get("search", ""), 100)
    brand_id = request.args.get("brand_id")
    low_stock = request.args.get("low_stock")

    query = PRODUCT_SELECT + " WHERE 1=1"
    params: list[Any] = []
    if search:
        query += " AND (p.model LIKE %s OR b.name LIKE %s OR p.color LIKE %s)"
        term = f"%{search}%"
        params.extend([term, term, term])
    if brand_id:
        query += " AND p.brand_id=%s"
        params.append(brand_id)
    if low_stock == "1":
        query += " AND p.stock <= 5"
    query += " ORDER BY p.product_id DESC"

    rows, _, _ = execute_query(query, tuple(params), fetch=True)
    return ok(serialize_rows(rows))


@app.get("/api/products/<int:product_id>")
@login_required
def get_product(product_id: int):
    rows, _, _ = execute_query(PRODUCT_SELECT + " WHERE p.product_id=%s", (product_id,), fetch=True)
    if not rows:
        return fail("Product not found.", 404)
    return ok(serialize_row(rows[0]))


@app.post("/api/products")
@role_required("admin")
def create_product():
    try:
        p = product_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return fail(str(exc))

    try:
        _, product_id, _ = execute_query(
            """
            INSERT INTO products
                (brand_id, model, ram, storage, color, price, stock, description, supplier_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                p["brand_id"], p["model"], p["ram"], p["storage"], p["color"],
                p["price"], p["stock"], p["description"], p["supplier_id"],
            ),
        )
        return ok({"id": product_id}, "Product created.", 201)
    except Error as exc:
        return fail(f"Unable to create product: {exc}", 500)


@app.put("/api/products/<int:product_id>")
@role_required("admin")
def update_product(product_id: int):
    try:
        p = product_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return fail(str(exc))

    try:
        exists, _, _ = execute_query("SELECT product_id FROM products WHERE product_id=%s", (product_id,), fetch=True)
        if not exists:
            return fail("Product not found.", 404)
        execute_query(
            """
            UPDATE products
            SET brand_id=%s, model=%s, ram=%s, storage=%s, color=%s,
                price=%s, stock=%s, description=%s, supplier_id=%s
            WHERE product_id=%s
            """,
            (
                p["brand_id"], p["model"], p["ram"], p["storage"], p["color"],
                p["price"], p["stock"], p["description"], p["supplier_id"], product_id,
            ),
        )
        return ok(message="Product updated.")
    except Error as exc:
        return fail(f"Unable to update product: {exc}", 500)


@app.delete("/api/products/<int:product_id>")
@role_required("admin")
def delete_product(product_id: int):
    rows, _, _ = execute_query("SELECT COUNT(*) AS count FROM sale_items WHERE product_id=%s", (product_id,), fetch=True)
    if rows[0]["count"]:
        return fail("This product has sales history and cannot be deleted. Set stock to 0 instead.", 409)
    try:
        _, _, affected = execute_query("DELETE FROM products WHERE product_id=%s", (product_id,))
        if not affected:
            return fail("Product not found.", 404)
        return ok(message="Product deleted.")
    except Error as exc:
        return fail(f"Unable to delete product: {exc}", 500)


@app.post("/api/products/<int:product_id>/restock")
@role_required("admin")
def restock_product(product_id: int):
    data = request.get_json(silent=True) or {}
    qty = parse_nonnegative_int(data.get("quantity"))
    if not qty or qty <= 0:
        return fail("Enter a valid restock quantity greater than zero.")

    user = current_user()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        connection.start_transaction()
        cursor.execute("SELECT product_id, model, stock FROM products WHERE product_id=%s FOR UPDATE", (product_id,))
        p = cursor.fetchone()
        if p is None:
            connection.rollback()
            return fail("Product not found.", 404)

        if user is None:
            connection.rollback()
            return fail("Please log in first.", 401)
        new_stock = p["stock"] + qty
        cursor.execute("UPDATE products SET stock=%s WHERE product_id=%s", (new_stock, product_id))
        cursor.execute(
            """
            INSERT INTO stock_logs (product_id, change_amount, reason, user_id)
            VALUES (%s, %s, 'Restock', %s)
            """,
            (product_id, qty, user["user_id"]),
        )
        connection.commit()
        return ok({"product_id": product_id, "new_stock": new_stock, "added": qty}, f"Restocked {qty} unit(s) of {p['model']}.")
    except Error as exc:
        if connection:
            connection.rollback()
        return fail(f"Restock failed: {exc}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ============================================================
# CUSTOMERS
# ============================================================

CUSTOMER_SELECT = """
SELECT c.customer_id AS id, c.name, c.phone, c.email, c.city, c.address,
       c.created_at, c.updated_at,
       COUNT(DISTINCT s.sale_id) AS order_count,
       COALESCE(SUM(CASE WHEN s.status <> 'Cancelled' THEN s.total_amount ELSE 0 END),0) AS spent
FROM customers c
LEFT JOIN sales s ON s.customer_id=c.customer_id
"""


@app.get("/api/customers")
@role_required("admin")
def get_customers():
    search = clean_text(request.args.get("search", ""), 100)
    query = CUSTOMER_SELECT
    params: list[Any] = []
    if search:
        query += " WHERE c.name LIKE %s OR c.phone LIKE %s OR c.email LIKE %s"
        term = f"%{search}%"
        params.extend([term, term, term])
    query += " GROUP BY c.customer_id ORDER BY c.customer_id DESC"
    rows, _, _ = execute_query(query, tuple(params), fetch=True)
    return ok(serialize_rows(rows))


@app.get("/api/customers/<int:customer_id>")
@login_required
def get_customer(customer_id: int):
    user = current_user()
    if user is None:
        return fail("Please log in first.", 401)
    if user["role"] != "admin" and user.get("customer_id") != customer_id:
        return fail("You do not have permission to view this customer.", 403)
    rows, _, _ = execute_query(CUSTOMER_SELECT + " WHERE c.customer_id=%s GROUP BY c.customer_id", (customer_id,), fetch=True)
    if not rows:
        return fail("Customer not found.", 404)
    return ok(serialize_row(rows[0]))


@app.post("/api/customers")
@role_required("admin")
def create_customer():
    data = request.get_json(silent=True) or {}
    missing = required(data, ["name", "phone", "email"])
    if missing:
        return fail("Missing fields: " + ", ".join(missing))
    name = clean_text(data["name"], 120)
    phone = clean_text(data["phone"], 20)
    email = clean_text(data["email"], 160).lower()
    username = clean_text(data.get("username", ""), 80)
    password = str(data.get("password", ""))
    if not validate_phone(phone):
        return fail("Enter a valid phone number.")
    if username and len(username) < 3:
        return fail("Username must contain at least 3 characters.")
    if username and len(password) < 6:
        return fail("Customer login password must contain at least 6 characters.")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        cursor.execute("SELECT customer_id FROM customers WHERE phone=%s OR email=%s", (phone, email))
        if cursor.fetchone():
            return fail("Phone or email is already in use.", 409)
        if username:
            cursor.execute("SELECT user_id FROM users WHERE username=%s OR email=%s", (username, email))
            if cursor.fetchone():
                return fail("Username or email is already used by a login account.", 409)

        cursor.execute(
            "INSERT INTO customers (name,phone,email,city,address) VALUES (%s,%s,%s,%s,%s)",
            (name, phone, email, clean_text(data.get("city"), 80), clean_text(data.get("address"), 255)),
        )
        customer_id = cursor.lastrowid
        if username:
            cursor.execute(
                "INSERT INTO users (username,email,password_hash,role,customer_id) VALUES (%s,%s,%s,'customer',%s)",
                (username, email, password, customer_id),
            )
        connection.commit()
        return ok({"id": customer_id, "login_created": bool(username)}, "Customer created.", 201)
    except Error as exc:
        if connection:
            connection.rollback()
        return fail(f"Unable to create customer: {exc}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.put("/api/customers/<int:customer_id>")
@login_required
def update_customer(customer_id: int):
    user = current_user()
    if user is None:
        return fail("Please log in first.", 401)
    if user["role"] != "admin" and user.get("customer_id") != customer_id:
        return fail("You do not have permission to update this customer.", 403)

    data = request.get_json(silent=True) or {}
    missing = required(data, ["name", "phone", "email"])
    if missing:
        return fail("Missing fields: " + ", ".join(missing))
    name = clean_text(data["name"], 120)
    phone = clean_text(data["phone"], 20)
    email = clean_text(data["email"], 160).lower()
    new_password = str(data.get("new_password", ""))
    if not validate_phone(phone):
        return fail("Enter a valid phone number.")
    if new_password and len(new_password) < 6:
        return fail("New password must contain at least 6 characters.")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        connection.start_transaction()

        # Use SELECT FOR UPDATE to lock and verify the customer exists.
        cursor.execute("SELECT customer_id FROM customers WHERE customer_id=%s FOR UPDATE", (customer_id,))
        if not cursor.fetchone():
            connection.rollback()
            return fail("Customer not found.", 404)

        cursor.execute(
            """
            UPDATE customers SET name=%s,phone=%s,email=%s,city=%s,address=%s
            WHERE customer_id=%s
            """,
            (name, phone, email, clean_text(data.get("city"), 80), clean_text(data.get("address"), 255), customer_id),
        )

        # Sync the linked login account (if one exists).
        cursor.execute("SELECT user_id FROM users WHERE customer_id=%s FOR UPDATE", (customer_id,))
        linked_user = cursor.fetchone()

        if linked_user:
            # Keep the linked login account synchronized with customer email.
            cursor.execute("UPDATE users SET email=%s WHERE customer_id=%s", (email, customer_id))
            if new_password:
                cursor.execute(
                    "UPDATE users SET password_hash=%s WHERE customer_id=%s",
                    (generate_password_hash(new_password), customer_id),
                )

        connection.commit()
        return ok(message="Customer profile updated successfully.")
    except Error as exc:
        if connection:
            connection.rollback()
        if getattr(exc, "errno", None) == 1062:
            return fail("Phone or email is already in use.", 409)
        return fail(f"Unable to update customer: {exc}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.delete("/api/customers/<int:customer_id>")
@role_required("admin")
def delete_customer(customer_id: int):
    rows, _, _ = execute_query("SELECT COUNT(*) AS count FROM sales WHERE customer_id=%s", (customer_id,), fetch=True)
    if rows[0]["count"]:
        return fail("This customer has order history and cannot be deleted.", 409)
    try:
        # Delete linked user account first (FK constraint)
        execute_query("DELETE FROM users WHERE customer_id=%s", (customer_id,))
        _, _, affected = execute_query("DELETE FROM customers WHERE customer_id=%s", (customer_id,))
        if not affected:
            return fail("Customer not found.", 404)
        return ok(message="Customer deleted.")
    except Error as exc:
        return fail(f"Unable to delete customer: {exc}", 500)


# ============================================================
# SUPPLIERS
# ============================================================

@app.get("/api/suppliers")
@role_required("admin")
def get_suppliers():
    search = clean_text(request.args.get("search", ""), 100)
    query = """
        SELECT s.supplier_id AS id, s.name, s.phone, s.email, s.address,
               COUNT(p.product_id) AS product_count, s.created_at, s.updated_at
        FROM suppliers s
        LEFT JOIN products p ON p.supplier_id=s.supplier_id
    """
    params: list[Any] = []
    if search:
        query += " WHERE s.name LIKE %s OR s.phone LIKE %s OR s.email LIKE %s"
        term = f"%{search}%"
        params.extend([term, term, term])
    query += " GROUP BY s.supplier_id ORDER BY s.supplier_id DESC"
    rows, _, _ = execute_query(query, tuple(params), fetch=True)
    return ok(serialize_rows(rows))


@app.post("/api/suppliers")
@role_required("admin")
def create_supplier():
    data = request.get_json(silent=True) or {}
    missing = required(data, ["name", "phone", "email", "address"])
    if missing:
        return fail("Missing fields: " + ", ".join(missing))
    phone = clean_text(data["phone"], 20)
    if not validate_phone(phone):
        return fail("Enter a valid phone number.")
    try:
        _, supplier_id, _ = execute_query(
            "INSERT INTO suppliers (name,phone,email,address) VALUES (%s,%s,%s,%s)",
            (clean_text(data["name"], 120), phone, clean_text(data["email"], 160).lower(), clean_text(data["address"], 255)),
        )
        return ok({"id": supplier_id}, "Supplier created.", 201)
    except Error as exc:
        if exc.errno == 1062:
            return fail("Supplier phone or email is already in use.", 409)
        return fail(f"Unable to create supplier: {exc}", 500)


@app.put("/api/suppliers/<int:supplier_id>")
@role_required("admin")
def update_supplier(supplier_id: int):
    data = request.get_json(silent=True) or {}
    missing = required(data, ["name", "phone", "email", "address"])
    if missing:
        return fail("Missing fields: " + ", ".join(missing))
    phone = clean_text(data["phone"], 20)
    if not validate_phone(phone):
        return fail("Enter a valid phone number.")
    try:
        exists, _, _ = execute_query("SELECT supplier_id FROM suppliers WHERE supplier_id=%s", (supplier_id,), fetch=True)
        if not exists:
            return fail("Supplier not found.", 404)
        execute_query(
            """
            UPDATE suppliers SET name=%s,phone=%s,email=%s,address=%s
            WHERE supplier_id=%s
            """,
            (clean_text(data["name"], 120), phone, clean_text(data["email"], 160).lower(), clean_text(data["address"], 255), supplier_id),
        )
        return ok(message="Supplier updated.")
    except Error as exc:
        if exc.errno == 1062:
            return fail("Supplier phone or email is already in use.", 409)
        return fail(f"Unable to update supplier: {exc}", 500)


@app.delete("/api/suppliers/<int:supplier_id>")
@role_required("admin")
def delete_supplier(supplier_id: int):
    try:
        _, _, affected = execute_query("DELETE FROM suppliers WHERE supplier_id=%s", (supplier_id,))
        if not affected:
            return fail("Supplier not found.", 404)
        return ok(message="Supplier deleted.")
    except Error as exc:
        if exc.errno == 1451:
            return fail("This supplier is linked to products and cannot be deleted.", 409)
        return fail(f"Unable to delete supplier: {exc}", 500)


# ============================================================
# SALES / ORDERS
# ============================================================

SALE_LIST_SELECT = """
SELECT s.sale_id AS id, s.order_number, s.customer_id, c.name AS customer_name,
       s.sale_date, s.total_amount, s.payment_method AS payment, s.status, s.notes,
       COUNT(si.sale_item_id) AS item_lines
FROM sales s
JOIN customers c ON c.customer_id=s.customer_id
LEFT JOIN sale_items si ON si.sale_id=s.sale_id
"""


def get_sale_with_items(sale_id: int):
    sales, _, _ = execute_query(
        """
        SELECT s.sale_id AS id, s.order_number, s.customer_id, c.name AS customer_name,
               c.phone AS customer_phone, c.email AS customer_email,
               c.address AS customer_address, c.city AS customer_city,
               s.sale_date, s.total_amount, s.payment_method AS payment,
               s.status, s.notes, s.created_by, s.cancelled_at
        FROM sales s
        JOIN customers c ON c.customer_id=s.customer_id
        WHERE s.sale_id=%s
        """,
        (sale_id,),
        fetch=True,
    )
    if not sales:
        return None

    items, _, _ = execute_query(
        """
        SELECT si.sale_item_id AS id, si.product_id, p.model, b.name AS brand,
               si.quantity AS qty, si.unit_price, si.subtotal
        FROM sale_items si
        JOIN products p ON p.product_id=si.product_id
        JOIN brands b ON b.brand_id=p.brand_id
        WHERE si.sale_id=%s
        ORDER BY si.sale_item_id
        """,
        (sale_id,),
        fetch=True,
    )
    result = serialize_row(sales[0])
    result["items"] = serialize_rows(items)
    return result


def create_sale_transaction(
    customer_id: int,
    items: list[dict[str, Any]],
    payment: str,
    notes: str,
    created_by: int | None,
) -> int:
    if payment not in {"Cash", "Card", "UPI"}:
        raise ValueError("Invalid payment method.")
    if not isinstance(items, list) or not items:
        raise ValueError("At least one sale item is required.")

    # Normalize and merge duplicate product lines.
    merged: dict[int, int] = {}
    for item in items:
        try:
            product_id_value = item.get("product_id")
            quantity_value = item.get("quantity", item.get("qty", 0))
            if product_id_value is None or quantity_value is None:
                raise ValueError
            product_id = int(product_id_value)
            quantity = int(quantity_value)
        except (TypeError, ValueError):
            raise ValueError("Each sale item must have a valid product and quantity.")
        if product_id <= 0 or quantity <= 0:
            raise ValueError("Product and quantity must be positive.")
        merged[product_id] = merged.get(product_id, 0) + quantity

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        connection.start_transaction()

        # Customer must exist.
        cursor.execute("SELECT customer_id FROM customers WHERE customer_id=%s FOR UPDATE", (customer_id,))
        if not cursor.fetchone():
            raise ValueError("Customer not found.")

        sale_lines = []
        total = Decimal("0.00")

        for product_id, quantity in merged.items():
            cursor.execute(
                """
                SELECT product_id, model, price, stock
                FROM products
                WHERE product_id=%s
                FOR UPDATE
                """,
                (product_id,),
            )
            product = cursor.fetchone()
            if not product:
                raise ValueError(f"Product {product_id} was not found.")
            if product["stock"] < quantity:
                raise ValueError(
                    f"Only {product['stock']} unit(s) of {product['model']} are available."
                )
            subtotal = (Decimal(str(product["price"])) * quantity).quantize(Decimal("0.01"))
            total += subtotal
            sale_lines.append((product_id, quantity, Decimal(str(product["price"])), subtotal))

        order_number = f"ORD-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:6].upper()}"

        cursor.execute(
            """
            INSERT INTO sales
                (order_number, customer_id, total_amount, payment_method, status, notes, created_by)
            VALUES (%s,%s,%s,%s,'Placed',%s,%s)
            """,
            (order_number, customer_id, total, payment, clean_text(notes, 500), created_by),
        )
        sale_id = cursor.lastrowid
        if sale_id is None:
            raise RuntimeError("MySQL did not return an ID for the created order.")

        for product_id, quantity, unit_price, subtotal in sale_lines:
            cursor.execute(
                """
                INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,subtotal)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (sale_id, product_id, quantity, unit_price, subtotal),
            )
            cursor.execute(
                "UPDATE products SET stock=stock-%s WHERE product_id=%s",
                (quantity, product_id),
            )

        connection.commit()
        return sale_id
    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.get("/api/sales")
@login_required
def get_sales():
    user = current_user()
    if user is None:
        return fail("Please log in first.", 401)
    search = clean_text(request.args.get("search", ""), 100)
    status = clean_text(request.args.get("status", ""), 30)
    query = SALE_LIST_SELECT + " WHERE 1=1"
    params: list[Any] = []

    if user["role"] == "customer":
        query += " AND s.customer_id=%s"
        params.append(user["customer_id"])
    if search:
        query += " AND (s.order_number LIKE %s OR c.name LIKE %s)"
        term = f"%{search}%"
        params.extend([term, term])
    if status:
        query += " AND s.status=%s"
        params.append(status)

    query += " GROUP BY s.sale_id ORDER BY s.sale_id DESC"
    rows, _, _ = execute_query(query, tuple(params), fetch=True)
    result = serialize_rows(rows)

    # Return the line items for each order; this keeps frontend rendering simple.
    for sale in result:
        detail = get_sale_with_items(int(sale["id"]))
        sale["items"] = detail["items"] if detail is not None else []
    return ok(result)


@app.get("/api/sales/<int:sale_id>")
@login_required
def get_sale(sale_id: int):
    detail = get_sale_with_items(sale_id)
    if detail is None:
        return fail("Order not found.", 404)
    user = current_user()
    if user is None:
        return fail("Please log in first.", 401)
    if user["role"] == "customer":
        customer_id = user.get("customer_id")
        if customer_id is None:
            return fail("This customer account has no linked profile.", 403)
        if int(detail["customer_id"]) != int(customer_id):
            return fail("You do not have permission to view this order.", 403)
    return ok(detail)


@app.post("/api/sales")
@role_required("admin", "customer")
def create_sale():
    data = request.get_json(silent=True) or {}
    user = current_user()
    if user is None:
        return fail("Please log in first.", 401)
    if user["role"] == "customer":
        customer_id = user.get("customer_id")
        if customer_id is None:
            return fail("This customer account has no linked profile.", 403)
    else:
        customer_id = data.get("customer_id")
    customer_id = parse_nonnegative_int(customer_id)
    if customer_id is None or customer_id <= 0:
        return fail("Choose a valid customer.")

    try:
        sale_id = create_sale_transaction(
            customer_id=customer_id,
            items=data.get("items", []),
            payment=clean_text(data.get("payment_method", "UPI"), 20),
            notes=clean_text(data.get("notes", ""), 500),
            created_by=int(user["user_id"]),
        )
        return ok({"id": sale_id, "order": get_sale_with_items(sale_id)}, "Order created successfully.", 201)
    except ValueError as exc:
        return fail(str(exc), 400)
    except Error as exc:
        return fail(f"Unable to create order: {exc}", 500)


@app.put("/api/sales/<int:sale_id>")
@role_required("admin")
def update_sale(sale_id: int):
    data = request.get_json(silent=True) or {}
    new_status = clean_text(data.get("status"), 30)
    if new_status not in {"Placed", "Processing", "Completed", "Cancelled"}:
        return fail("Invalid order status.")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_dictionary_cursor(connection)
        connection.start_transaction()
        cursor.execute("SELECT * FROM sales WHERE sale_id=%s FOR UPDATE", (sale_id,))
        sale = cursor.fetchone()
        if not sale:
            connection.rollback()
            return fail("Order not found.", 404)

        old_status = sale["status"]
        if old_status == new_status:
            connection.commit()
            return ok(message="Order status is already set to that value.")

        # Once cancelled, an order cannot be reopened because its stock has already been restored.
        if old_status == "Cancelled":
            connection.rollback()
            return fail("Cancelled orders cannot be reopened.", 409)

        if new_status == "Cancelled":
            cursor.execute("SELECT product_id, quantity FROM sale_items WHERE sale_id=%s", (sale_id,))
            items = cursor.fetchall()
            for item in items:
                cursor.execute(
                    "UPDATE products SET stock=stock+%s WHERE product_id=%s",
                    (item["quantity"], item["product_id"]),
                )
            cursor.execute(
                "UPDATE sales SET status='Cancelled', cancelled_at=NOW() WHERE sale_id=%s",
                (sale_id,),
            )
        else:
            cursor.execute("UPDATE sales SET status=%s WHERE sale_id=%s", (new_status, sale_id))

        connection.commit()
        return ok(message="Order status updated.")
    except Error as exc:
        if connection:
            connection.rollback()
        return fail(f"Unable to update order: {exc}", 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ============================================================
# DASHBOARD / REPORTS
# ============================================================

@app.get("/api/admin/summary")
@role_required("admin")
def admin_summary():
    queries = {
        "products": "SELECT COUNT(*) AS value FROM products",
        "stock": "SELECT COALESCE(SUM(stock),0) AS value FROM products",
        "brands": "SELECT COUNT(*) AS value FROM brands",
        "customers": "SELECT COUNT(*) AS value FROM customers",
        "suppliers": "SELECT COUNT(*) AS value FROM suppliers",
        "orders": "SELECT COUNT(*) AS value FROM sales",
        "today_sales": "SELECT COALESCE(SUM(total_amount),0) AS value FROM sales WHERE DATE(sale_date)=CURDATE() AND status <> 'Cancelled'",
        "revenue": "SELECT COALESCE(SUM(total_amount),0) AS value FROM sales WHERE status <> 'Cancelled'",
    }
    result = {}
    for key, query in queries.items():
        rows, _, _ = execute_query(query, fetch=True)
        result[key] = serialize_value(rows[0]["value"])

    low, _, _ = execute_query(
        """
        SELECT p.product_id AS id, p.model, b.name AS brand, p.stock, p.price
        FROM products p JOIN brands b ON b.brand_id=p.brand_id
        WHERE p.stock <= 5 ORDER BY p.stock ASC, p.model LIMIT 8
        """,
        fetch=True,
    )
    recent, _, _ = execute_query(
        """
        SELECT s.sale_id AS id, s.order_number, c.name AS customer_name,
               DATE_FORMAT(s.sale_date,'%Y-%m-%d') AS date,
               s.total_amount, s.payment_method AS payment, s.status
        FROM sales s JOIN customers c ON c.customer_id=s.customer_id
        ORDER BY s.sale_id DESC LIMIT 8
        """,
        fetch=True,
    )
    result["low_stock"] = serialize_rows(low)
    result["recent_orders"] = serialize_rows(recent)
    return ok(result)


@app.get("/api/customer/summary")
@role_required("customer")
def customer_summary():
    user = current_user()
    if user is None:
        return fail("Please log in first.", 401)
    rows, _, _ = execute_query(
        """
        SELECT COUNT(*) AS orders,
               COALESCE(SUM(CASE WHEN status<>'Cancelled' THEN total_amount ELSE 0 END),0) AS spent
        FROM sales WHERE customer_id=%s
        """,
        (user["customer_id"],),
        fetch=True,
    )
    return ok({
        "cart_items": 0,
        "orders": rows[0]["orders"],
        "spent": serialize_value(rows[0]["spent"]),
    })


@app.get("/api/reports")
@role_required("admin")
def reports():
    sales_by_day, _, _ = execute_query(
        """
        SELECT DATE(sale_date) AS day, COALESCE(SUM(total_amount),0) AS revenue,
               COUNT(*) AS orders
        FROM sales
        WHERE status <> 'Cancelled' AND sale_date >= DATE_SUB(CURDATE(), INTERVAL 29 DAY)
        GROUP BY DATE(sale_date)
        ORDER BY day
        """,
        fetch=True,
    )
    top_products, _, _ = execute_query(
        """
        SELECT p.model, b.name AS brand, SUM(si.quantity) AS units,
               SUM(si.subtotal) AS revenue
        FROM sale_items si
        JOIN sales s ON s.sale_id=si.sale_id AND s.status <> 'Cancelled'
        JOIN products p ON p.product_id=si.product_id
        JOIN brands b ON b.brand_id=p.brand_id
        GROUP BY p.product_id
        ORDER BY units DESC, revenue DESC
        LIMIT 10
        """,
        fetch=True,
    )
    stock, _, _ = execute_query(
        """
        SELECT p.product_id AS id, p.model, b.name AS brand, p.stock, p.price,
               (p.stock*p.price) AS stock_value
        FROM products p JOIN brands b ON b.brand_id=p.brand_id
        ORDER BY p.stock ASC, p.model
        """,
        fetch=True,
    )
    payment, _, _ = execute_query(
        """
        SELECT payment_method AS payment, COUNT(*) AS orders,
               COALESCE(SUM(total_amount),0) AS revenue
        FROM sales WHERE status <> 'Cancelled'
        GROUP BY payment_method ORDER BY revenue DESC
        """,
        fetch=True,
    )
    top_customers, _, _ = execute_query(
        """
        SELECT c.customer_id AS id, c.name, COUNT(s.sale_id) AS orders,
               COALESCE(SUM(CASE WHEN s.status <> 'Cancelled' THEN s.total_amount ELSE 0 END),0) AS spent
        FROM customers c
        LEFT JOIN sales s ON s.customer_id=c.customer_id
        GROUP BY c.customer_id
        ORDER BY spent DESC, orders DESC, c.name
        LIMIT 10
        """,
        fetch=True,
    )
    return ok({
        "sales_by_day": serialize_rows(sales_by_day),
        "top_products": serialize_rows(top_products),
        "stock": serialize_rows(stock),
        "payment": serialize_rows(payment),
        "top_customers": serialize_rows(top_customers),
    })


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return fail("API endpoint not found.", 404)
    return "Page not found", 404


@app.errorhandler(405)
def method_not_allowed(_error):
    if request.path.startswith("/api/"):
        return fail("HTTP method not allowed for this endpoint.", 405)
    return "Method not allowed", 405


@app.errorhandler(Exception)
def unhandled_error(error):
    app.logger.exception("Unhandled server error", exc_info=error)
    if request.path.startswith("/api/"):
        return fail("An unexpected server error occurred.", 500)
    return "An unexpected server error occurred.", 500


# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":
    try:
        init_database()
        print("=" * 64)
        print(" Mobile Shop Management System")
        print(" Flask + MySQL backend")
        print("=" * 64)
        print(f" MySQL: {DB_HOST}:{DB_PORT}/{DB_NAME}")
        print(" Admin login   : admin / admin123")
        print(" Customer login: customer / customer123")
        print(" Open browser  : http://127.0.0.1:5000")
        print("=" * 64)
    except Error as exc:
        print("DATABASE STARTUP ERROR")
        print(exc)
        print("Update DB_PASSWORD (or MYSQL_PASSWORD) in app.py/environment and make sure MySQL is running.")
        raise SystemExit(1)

    app.run(host="127.0.0.1", port=5000, debug=True)
