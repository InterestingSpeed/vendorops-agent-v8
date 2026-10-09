SCHEMA = r"""
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_code TEXT UNIQUE NOT NULL,
    legal_name TEXT NOT NULL,
    trade_name TEXT,
    gstin TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('APPROVED','PROVISIONAL','BLOCKED','INACTIVE')),
    address TEXT,
    state_code TEXT,
    contact_name TEXT,
    contact_email TEXT,
    bank_verified INTEGER NOT NULL DEFAULT 0 CHECK(bank_verified IN (0,1)),
    procurement_approved INTEGER NOT NULL DEFAULT 0 CHECK(procurement_approved IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gst_registry (
    gstin TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    trade_name TEXT,
    registration_status TEXT NOT NULL CHECK(registration_status IN ('ACTIVE','CANCELLED','SUSPENDED')),
    registration_date TEXT,
    constitution TEXT,
    address TEXT,
    state_code TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT UNIQUE NOT NULL,
    vendor_id INTEGER,
    supplier_gstin TEXT NOT NULL,
    description TEXT NOT NULL,
    ordered_qty REAL NOT NULL CHECK(ordered_qty > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    total_amount REAL NOT NULL CHECK(total_amount >= 0),
    amount_consumed REAL NOT NULL DEFAULT 0 CHECK(amount_consumed >= 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED','CANCELLED')),
    expected_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(vendor_id) REFERENCES vendors(id)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    supplier_gstin TEXT NOT NULL,
    vendor_id INTEGER,
    po_number TEXT,
    invoice_date TEXT,
    amount REAL,
    source_file TEXT,
    extraction_method TEXT,
    extraction_confidence REAL CHECK(extraction_confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL CHECK(status IN ('RECEIVED','BLOCKED','GATE_CREATED','DUPLICATE_REVIEW','APPROVAL_PENDING')),
    created_at TEXT NOT NULL,
    UNIQUE(supplier_gstin, invoice_number),
    FOREIGN KEY(vendor_id) REFERENCES vendors(id)
);

CREATE TABLE IF NOT EXISTS gate_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_entry_number TEXT UNIQUE NOT NULL,
    invoice_id INTEGER UNIQUE NOT NULL,
    po_number TEXT NOT NULL,
    vendor_id INTEGER NOT NULL,
    vehicle_number TEXT,
    status TEXT NOT NULL CHECK(status IN ('CREATED','BLOCKED','CANCELLED')),
    reason TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    FOREIGN KEY(invoice_id) REFERENCES invoices(id),
    FOREIGN KEY(vendor_id) REFERENCES vendors(id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL CHECK(channel IN ('EMAIL','IN_APP')),
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('SENT','FAILED')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    policy_key TEXT PRIMARY KEY,
    value_text TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_faults (
    service TEXT NOT NULL,
    lookup_key TEXT NOT NULL,
    remaining_failures INTEGER NOT NULL DEFAULT 0 CHECK(remaining_failures >= 0),
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY(service, lookup_key)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vendors_gstin ON vendors(gstin);
CREATE INDEX IF NOT EXISTS idx_po_gstin ON purchase_orders(supplier_gstin);
CREATE INDEX IF NOT EXISTS idx_invoices_gstin_no ON invoices(supplier_gstin, invoice_number);
CREATE INDEX IF NOT EXISTS idx_gate_created_at ON gate_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
"""
