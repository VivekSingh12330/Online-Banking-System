from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone
import secrets
import os

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Database initialization
def initialize_database():
    conn = sqlite3.connect('bank.db')
    cursor = conn.cursor()
    
    cursor.execute("DROP TABLE IF EXISTS transactions")
    cursor.execute("DROP TABLE IF EXISTS users")
    cursor.execute("DROP TABLE IF EXISTS accounts")
    
    cursor.execute('''CREATE TABLE accounts (
                    account_number TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    balance REAL)''')
    
    cursor.execute('''CREATE TABLE users (
                    username TEXT PRIMARY KEY,
                    account_number TEXT UNIQUE,
                    password_hash TEXT NOT NULL)''')
    
    cursor.execute('''CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_number TEXT,
                    type TEXT,
                    amount REAL,
                    related_account TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    # Add sample data for testing
    cursor.execute("INSERT INTO accounts VALUES ('1234567890', 'Test User', 10000.00)")
    cursor.execute("INSERT INTO users VALUES ('test', '1234567890', ?)", 
                  (hashlib.sha256('test123'.encode()).hexdigest(),))
    
    conn.commit()
    conn.close()

# Helper functions
def get_db_connection():
    conn = sqlite3.connect('bank.db')
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Custom filter for Indian number formatting
@app.template_filter('indian_format')
def indian_number_format(value):
    value = float(value)
    if value < 1000:
        return "{:,.2f}".format(value)
    else:
        value = str(value).split('.')[0]
        last_three = value[-3:]
        other_numbers = value[:-3]
        if other_numbers:
            formatted = other_numbers[::-1].replace('', ',')[1:-1][::-1] + ',' + last_three
        else:
            formatted = last_three
        return formatted

# Routes
@app.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and user['password_hash'] == hash_password(password):
            session['username'] = user['username']
            session['account_number'] = user['account_number']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        name = request.form['name']
        initial_deposit = float(request.form.get('initial_deposit', 0))
        
        conn = get_db_connection()
        
        # Check if username exists
        if conn.execute('SELECT username FROM users WHERE username = ?', (username,)).fetchone():
            flash('Username already exists', 'danger')
            conn.close()
            return redirect(url_for('register'))
        
        # Create account
        account_number = str(int(datetime.now().timestamp()))[-10:]
        conn.execute('INSERT INTO accounts VALUES (?, ?, ?)', 
                   (account_number, name, initial_deposit))
        
        # Create user
        conn.execute('INSERT INTO users VALUES (?, ?, ?)', 
                   (username, account_number, hash_password(password)))
        
        conn.commit()
        conn.close()
        
        flash(f'Registration successful! Your account number is {account_number}', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    account = conn.execute('SELECT * FROM accounts WHERE account_number = ?', 
                         (session['account_number'],)).fetchone()
    transactions = conn.execute('''SELECT * FROM transactions 
                                WHERE account_number = ? 
                                ORDER BY timestamp DESC LIMIT 5''',
                              (session['account_number'],)).fetchall()
    conn.close()
    
    return render_template('dashboard.html', 
                         account=account, 
                         transactions=transactions)

@app.route('/deposit', methods=['POST'])
def deposit():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    amount = float(request.form['amount'])
    if amount <= 0:
        flash('Deposit amount must be positive', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    
    # Update balance
    conn.execute('UPDATE accounts SET balance = balance + ? WHERE account_number = ?',
               (amount, session['account_number']))
    
    # Record transaction
    conn.execute('INSERT INTO transactions (account_number, type, amount) VALUES (?, ?, ?)',
               (session['account_number'], 'Deposit', amount))
    
    conn.commit()
    conn.close()
    
    flash(f'Successfully deposited Rupees {amount:,.2f}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/withdraw', methods=['POST'])
def withdraw():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    amount = float(request.form['amount'])
    
    conn = get_db_connection()
    account = conn.execute('SELECT balance FROM accounts WHERE account_number = ?',
                         (session['account_number'],)).fetchone()
    
    if amount <= 0:
        flash('Withdrawal amount must be positive', 'danger')
    elif amount > account['balance']:
        flash('Insufficient funds', 'danger')
    else:
        # Update balance
        conn.execute('UPDATE accounts SET balance = balance - ? WHERE account_number = ?',
                   (amount, session['account_number']))
        
        # Record transaction
        conn.execute('INSERT INTO transactions (account_number, type, amount) VALUES (?, ?, ?)',
                   (session['account_number'], 'Withdrawal', amount))
        
        conn.commit()
        flash(f'Successfully withdrew Rupees {amount:,.2f}', 'success')
    
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/transfer', methods=['POST'])
def transfer():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    to_account = request.form['to_account']
    amount = float(request.form['amount'])
    
    if to_account == session['account_number']:
        flash("Cannot transfer to your own account", 'danger')
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    
    # Check recipient exists
    recipient = conn.execute('SELECT name FROM accounts WHERE account_number = ?',
                           (to_account,)).fetchone()
    if not recipient:
        flash('Recipient account not found', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    
    # Check sufficient balance
    sender_balance = conn.execute('SELECT balance FROM accounts WHERE account_number = ?',
                                (session['account_number'],)).fetchone()['balance']
    if amount > sender_balance:
        flash('Insufficient funds', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    
    # Perform transfer
    try:
        # Deduct from sender
        conn.execute('UPDATE accounts SET balance = balance - ? WHERE account_number = ?',
                   (amount, session['account_number']))
        
        # Add to recipient
        conn.execute('UPDATE accounts SET balance = balance + ? WHERE account_number = ?',
                   (amount, to_account))
        
        # Record transactions
        conn.execute('''INSERT INTO transactions 
                      (account_number, type, amount, related_account) 
                      VALUES (?, ?, ?, ?)''',
                   (session['account_number'], 'Transfer Sent', amount, to_account))
        conn.execute('''INSERT INTO transactions 
                      (account_number, type, amount, related_account) 
                      VALUES (?, ?, ?, ?)''',
                   (to_account, 'Transfer Received', amount, session['account_number']))
        
        conn.commit()
        flash(f'Successfully transferred Rupees {amount:,.2f} to account {to_account}', 'success')
    except:
        conn.rollback()
        flash('Transfer failed. Please try again.', 'danger')
    finally:
        conn.close()
    
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('home'))

# Create templates directory and HTML files
os.makedirs('templates', exist_ok=True)

# Base template
with open('templates/base.html', 'w', encoding='utf-8') as f:
    f.write('''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Simple Bank - {% block title %}{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .currency-input { position: relative; }
        .currency-input input { padding-left: 30px; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container">
            <a class="navbar-brand" href="/">Simple Bank</a>
            <div class="navbar-nav">
                {% if 'username' in session %}
                    <span class="nav-item text-white me-3">Welcome, {{ session['username'] }}</span>
                    <a class="nav-item nav-link" href="{{ url_for('logout') }}">Logout</a>
                {% else %}
                    <a class="nav-item nav-link" href="{{ url_for('login') }}">Login</a>
                    <a class="nav-item nav-link" href="{{ url_for('register') }}">Register</a>
                {% endif %}
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>''')

# Login template
with open('templates/login.html', 'w', encoding='utf-8') as f:
    f.write('''{% extends "base.html" %}

{% block title %}Login{% endblock %}

{% block content %}
<div class="row justify-content-center">
    <div class="col-md-6">
        <div class="card">
            <div class="card-header bg-primary text-white">
                <h4 class="mb-0">Login</h4>
            </div>
            <div class="card-body">
                <form method="POST" action="{{ url_for('login') }}">
                    <div class="mb-3">
                        <label for="username" class="form-label">Username</label>
                        <input type="text" class="form-control" id="username" name="username" required>
                    </div>
                    <div class="mb-3">
                        <label for="password" class="form-label">Password</label>
                        <input type="password" class="form-control" id="password" name="password" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Login</button>
                </form>
                <hr>
                <p class="text-center">Don't have an account? <a href="{{ url_for('register') }}">Register here</a></p>
            </div>
        </div>
    </div>
</div>
{% endblock %}''')

# Register template
with open('templates/register.html', 'w', encoding='utf-8') as f:
    f.write('''{% extends "base.html" %}

{% block title %}Register{% endblock %}

{% block content %}
<div class="row justify-content-center">
    <div class="col-md-6">
        <div class="card">
            <div class="card-header bg-primary text-white">
                <h4 class="mb-0">Register</h4>
            </div>
            <div class="card-body">
                <form method="POST" action="{{ url_for('register') }}">
                    <div class="mb-3">
                        <label for="username" class="form-label">Username</label>
                        <input type="text" class="form-control" id="username" name="username" required>
                    </div>
                    <div class="mb-3">
                        <label for="password" class="form-label">Password</label>
                        <input type="password" class="form-control" id="password" name="password" required>
                    </div>
                    <div class="mb-3">
                        <label for="name" class="form-label">Full Name</label>
                        <input type="text" class="form-control" id="name" name="name" required>
                    </div>
                    <div class="mb-3">
                        <label for="initial_deposit" class="form-label">Initial Deposit (Rupees)</label>
                        <input type="number" class="form-control" id="initial_deposit" name="initial_deposit" value="0" min="0" step="0.01" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Register</button>
                </form>
                <hr>
                <p class="text-center">Already have an account? <a href="{{ url_for('login') }}">Login here</a></p>
            </div>
        </div>
    </div>
</div>
{% endblock %}''')

# Dashboard template
with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write('''{% extends "base.html" %}

{% block title %}Dashboard{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-8">
        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h4 class="mb-0">Account Summary</h4>
            </div>
            <div class="card-body">
                <h5>Welcome, {{ account['name'] }}!</h5>
                <p>Account Number: {{ account['account_number'] }}</p>
                <p class="fs-3">Balance: Rupees {{ account['balance'] | indian_format }}</p>
            </div>
        </div>

        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h4 class="mb-0">Recent Transactions</h4>
            </div>
            <div class="card-body">
                {% if transactions %}
                    <div class="table-responsive">
                        <table class="table table-striped">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Type</th>
                                    <th>Amount (Rupees)</th>
                                    <th>Related Account</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for t in transactions %}
                                    <tr>
                                        <td>{{ t['timestamp'] }}</td>
                                        <td>{{ t['type'] }}</td>
                                        <td>{{ t['amount'] | indian_format }}</td>
                                        <td>{{ t['related_account'] or '-' }}</td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                {% else %}
                    <p class="text-center">No transactions yet.</p>
                {% endif %}
            </div>
        </div>
    </div>

    <div class="col-md-4">
        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h4 class="mb-0">Quick Actions</h4>
            </div>
            <div class="card-body">
                <form method="POST" action="{{ url_for('deposit') }}">
                    <div class="mb-3">
                        <label class="form-label">Deposit Money (Rupees)</label>
                        <div class="input-group">
                            <input type="number" class="form-control" name="amount" min="0.01" step="0.01" placeholder="Amount" required>
                            <button type="submit" class="btn btn-success">Deposit</button>
                        </div>
                    </div>
                </form>

                <form method="POST" action="{{ url_for('withdraw') }}">
                    <div class="mb-3">
                        <label class="form-label">Withdraw Money (Rupees)</label>
                        <div class="input-group">
                            <input type="number" class="form-control" name="amount" min="0.01" step="0.01" placeholder="Amount" required>
                            <button type="submit" class="btn btn-warning">Withdraw</button>
                        </div>
                    </div>
                </form>

                <form method="POST" action="{{ url_for('transfer') }}">
                    <div class="mb-3">
                        <label class="form-label">Transfer Money (Rupees)</label>
                        <input type="text" class="form-control mb-2" name="to_account" placeholder="Recipient Account #" required>
                        <div class="input-group">
                            <input type="number" class="form-control" name="amount" min="0.01" step="0.01" placeholder="Amount" required>
                            <button type="submit" class="btn btn-primary">Transfer</button>
                        </div>
                    </div>
                </form>

                <a href="{{ url_for('logout') }}" class="btn btn-danger w-100 mt-3">Logout</a>
            </div>
        </div>
    </div>
</div>
{% endblock %}''')

if __name__ == '__main__':
    initialize_database()
    app.run(debug=True)
