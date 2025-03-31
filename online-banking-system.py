import sqlite3
import time
from functools import wraps
import jwt
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import os

# Configuration
SECRET_KEY = secrets.token_hex(32)  # Generate a random secret key
TOKEN_EXPIRATION_MINUTES = 30

# Database Helper Functions
def database_exists():
    """Check if database file and tables exist"""
    if not os.path.exists("bank.db"):
        return False
    
    conn = sqlite3.connect("bank.db")
    cursor = conn.cursor()
    
    try:
        # Check if all required tables exist
        tables = ['accounts', 'users', 'transactions']
        for table in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if not cursor.fetchone():
                return False
        return True
    finally:
        conn.close()

def initialize_database():
    """Initialize database only if it doesn't exist"""
    if database_exists():
        return
        
    conn = sqlite3.connect("bank.db")
    cursor = conn.cursor()
    
    # Create tables with proper schema (using IF NOT EXISTS)
    cursor.execute('''CREATE TABLE IF NOT EXISTS accounts (
                    account_number TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    balance REAL)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    account_number TEXT UNIQUE,
                    password_hash TEXT NOT NULL,
                    FOREIGN KEY(account_number) REFERENCES accounts(account_number))''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_number TEXT,
                    type TEXT,
                    amount REAL,
                    related_account TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(account_number) REFERENCES accounts(account_number))''')
    
    # Only add sample data if no users exist
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO accounts VALUES ('1234567890', 'Test User', 10000.00)")
        cursor.execute("INSERT INTO users VALUES ('test', '1234567890', ?)", 
                      (hashlib.sha256('test123'.encode()).hexdigest(),))
    
    conn.commit()
    conn.close()

def backup_database():
    """Create timestamped backup of the database"""
    if not os.path.exists("bank.db"):
        return
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"bank_backup_{timestamp}.db"
    with open("bank.db", 'rb') as original:
        with open(backup_file, 'wb') as backup:
            backup.write(original.read())
    print(f"Database backed up to {backup_file}")

# Middleware Decorators
def authenticate(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not hasattr(self, 'current_user') or not self.current_user:
            print("Please login first.")
            return
        return func(self, *args, **kwargs)
    return wrapper

rate_limit_cache = {}

def rate_limiter(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not hasattr(self, 'current_user'):
            account_number = "anonymous"
        else:
            account_number = self.current_user['account_number']
            
        current_time = time.time()
        if account_number in rate_limit_cache and current_time - rate_limit_cache[account_number] < 2:
            print("Too many requests. Please wait.")
            return
        rate_limit_cache[account_number] = current_time
        return func(self, *args, **kwargs)
    return wrapper

def error_handler(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            print(f"An error occurred: {e}")
    return wrapper

class Bank:
    def __init__(self):
        self.conn = sqlite3.connect("bank.db")
        self.cursor = self.conn.cursor()
        self.current_user = None
        self.token = None

    def _hash_password(self, password):
        """Hash a password for storing."""
        return hashlib.sha256(password.encode()).hexdigest()

    def _verify_password(self, stored_hash, provided_password):
        """Verify a stored password against one provided by user"""
        return stored_hash == self._hash_password(provided_password)

    def _generate_token(self, username, account_number):
        """Generate JWT token"""
        expiration = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRATION_MINUTES)
        payload = {
            'username': username,
            'account_number': account_number,
            'exp': expiration
        }
        return jwt.encode(payload, SECRET_KEY, algorithm='HS256')

    def _verify_token(self, token):
        """Verify JWT token"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
            return payload
        except jwt.ExpiredSignatureError:
            print("Token has expired. Please login again.")
            return None
        except jwt.InvalidTokenError:
            print("Invalid token. Please login again.")
            return None

    @error_handler
    def register(self, username, password, name, initial_deposit=0.0):
        """Register a new user with a new account"""
        # Check if username already exists
        self.cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
        if self.cursor.fetchone():
            print("Username already exists.")
            return False
        
        # Generate account number
        account_number = str(int(time.time()))[-10:]  # Simple account number generation
        
        # Create account
        self.cursor.execute("INSERT INTO accounts VALUES (?, ?, ?)", 
                          (account_number, name, initial_deposit))
        
        # Create user
        password_hash = self._hash_password(password)
        self.cursor.execute("INSERT INTO users VALUES (?, ?, ?)", 
                          (username, account_number, password_hash))
        
        self.conn.commit()
        print(f"Registration successful. Your account number is {account_number}. You can now login.")
        return True

    @error_handler
    def login(self, username, password):
        """Authenticate user"""
        self.cursor.execute("SELECT username, password_hash, account_number FROM users WHERE username = ?", (username,))
        user = self.cursor.fetchone()
        
        if not user or not self._verify_password(user[1], password):
            print("Invalid username or password.")
            return False
        
        self.token = self._generate_token(user[0], user[2])
        self.current_user = {
            'username': user[0],
            'account_number': user[2]
        }
        
        # Get account details
        self.cursor.execute("SELECT name, balance FROM accounts WHERE account_number = ?", (user[2],))
        account_details = self.cursor.fetchone()
        self.current_user['name'] = account_details[0]
        self.current_user['balance'] = account_details[1]
        
        print(f"Login successful. Welcome {account_details[0]}!")
        return True

    def logout(self):
        """Logout current user"""
        self.current_user = None
        self.token = None
        print("Logged out successfully.")

    @authenticate
    @rate_limiter
    def deposit(self, amount):
        amount = float(amount)
        if amount > 0:
            self.cursor.execute("UPDATE accounts SET balance = balance + ? WHERE account_number = ?", 
                              (amount, self.current_user['account_number']))
            self.cursor.execute("INSERT INTO transactions (account_number, type, amount) VALUES (?, 'Deposit', ?)", 
                              (self.current_user['account_number'], amount))
            self.conn.commit()
            self.current_user['balance'] += amount
            print(f"{amount} deposited successfully. New balance: {self.current_user['balance']:.2f}")
        else:
            print("Deposit amount must be positive.")

    @authenticate
    @rate_limiter
    def withdraw(self, amount):
        amount = float(amount)
        if amount > self.current_user['balance']:
            print("Insufficient balance.")
        elif amount <= 0:
            print("Withdrawal amount must be positive.")
        else:
            self.cursor.execute("UPDATE accounts SET balance = balance - ? WHERE account_number = ?", 
                              (amount, self.current_user['account_number']))
            self.cursor.execute("INSERT INTO transactions (account_number, type, amount) VALUES (?, 'Withdraw', ?)", 
                              (self.current_user['account_number'], amount))
            self.conn.commit()
            self.current_user['balance'] -= amount
            print(f"{amount} withdrawn successfully. New balance: {self.current_user['balance']:.2f}")

    @authenticate
    def get_account_balance(self):
        print(f"Account Balance: {self.current_user['balance']:.2f}")

    @authenticate
    def display_account_details(self):
        print("\nAccount Details:")
        print(f"Account Holder: {self.current_user['name']}")
        print(f"Account Number: {self.current_user['account_number']}")
        print(f"Balance: {self.current_user['balance']:.2f}")

    @authenticate
    @rate_limiter
    def transfer_money(self, to_account, amount):
        if to_account == self.current_user['account_number']:
            print("Cannot transfer to your own account.")
            return
        
        # Check if target account exists
        self.cursor.execute("SELECT name FROM accounts WHERE account_number = ?", (to_account,))
        target_account = self.cursor.fetchone()
        if not target_account:
            print("Recipient account not found.")
            return
        
        amount = float(amount)
        if amount > self.current_user['balance']:
            print("Insufficient balance.")
            return
        
        self.conn.execute("BEGIN TRANSACTION")
        try:
            # Deduct from sender
            self.cursor.execute("UPDATE accounts SET balance = balance - ? WHERE account_number = ?", 
                              (amount, self.current_user['account_number']))
            
            # Add to recipient
            self.cursor.execute("UPDATE accounts SET balance = balance + ? WHERE account_number = ?", 
                              (amount, to_account))
            
            # Record transactions
            self.cursor.execute("INSERT INTO transactions (account_number, type, amount, related_account) VALUES (?, 'Transfer Sent', ?, ?)", 
                              (self.current_user['account_number'], amount, to_account))
            self.cursor.execute("INSERT INTO transactions (account_number, type, amount, related_account) VALUES (?, 'Transfer Received', ?, ?)", 
                              (to_account, amount, self.current_user['account_number']))
            
            self.conn.commit()
            self.current_user['balance'] -= amount
            print(f"{amount} transferred successfully to account {to_account}.")
            print(f"New balance: {self.current_user['balance']:.2f}")
        except:
            self.conn.rollback()
            print("Transfer failed. Please try again.")

    @authenticate
    def get_transaction_history(self):
        self.cursor.execute("SELECT type, amount, related_account, timestamp FROM transactions WHERE account_number = ? ORDER BY timestamp DESC LIMIT 10", 
                          (self.current_user['account_number'],))
        transactions = self.cursor.fetchall()
        
        if not transactions:
            print("No transactions found.")
            return
        
        print("\nLast 10 Transactions:")
        for t in transactions:
            if t[0] == 'Transfer Sent':
                print(f"{t[3]}: Transferred {t[1]:.2f} to account {t[2]}")
            elif t[0] == 'Transfer Received':
                print(f"{t[3]}: Received {t[1]:.2f} from account {t[2]}")
            else:
                print(f"{t[3]}: {t[0]} of {t[1]:.2f}")

    @authenticate
    def delete_account(self):
        confirm = input("Are you sure you want to delete your account? This cannot be undone. (yes/no): ")
        if confirm.lower() != 'yes':
            print("Account deletion cancelled.")
            return
        
        self.conn.execute("BEGIN TRANSACTION")
        try:
            # Delete transactions
            self.cursor.execute("DELETE FROM transactions WHERE account_number = ?", 
                              (self.current_user['account_number'],))
            
            # Delete user
            self.cursor.execute("DELETE FROM users WHERE account_number = ?", 
                              (self.current_user['account_number'],))
            
            # Delete account
            self.cursor.execute("DELETE FROM accounts WHERE account_number = ?", 
                              (self.current_user['account_number'],))
            
            self.conn.commit()
            print("Account deleted successfully.")
            self.logout()
        except:
            self.conn.rollback()
            print("Failed to delete account. Please try again.")

    def close_connection(self):
        self.conn.close()

def main_menu(bank):
    while True:
        if not bank.current_user:
            print("\n=== Welcome to Simple Bank ===")
            print("1. Login")
            print("2. Register")
            print("3. Exit")
            choice = input("Enter your choice: ")
            
            if choice == "1":
                username = input("Username: ")
                password = input("Password: ")
                bank.login(username, password)
            elif choice == "2":
                username = input("Choose a username: ")
                password = input("Choose a password: ")
                name = input("Your full name: ")
                initial_deposit = float(input("Initial deposit amount (0 if none): "))
                bank.register(username, password, name, initial_deposit)
            elif choice == "3":
                print("Goodbye!")
                return False
            else:
                print("Invalid choice. Please try again.")
        else:
            print(f"\n=== Welcome, {bank.current_user['name']} ===")
            print("1. Deposit")
            print("2. Withdraw")
            print("3. Check Balance")
            print("4. Account Details")
            print("5. Transfer Money")
            print("6. Transaction History")
            print("7. Delete Account")
            print("8. Logout")
            choice = input("Enter your choice: ")
            
            if choice == "1":
                amount = float(input("Enter deposit amount: "))
                bank.deposit(amount)
            elif choice == "2":
                amount = float(input("Enter withdrawal amount: "))
                bank.withdraw(amount)
            elif choice == "3":
                bank.get_account_balance()
            elif choice == "4":
                bank.display_account_details()
            elif choice == "5":
                to_account = input("Enter recipient account number: ")
                amount = float(input("Enter transfer amount: "))
                bank.transfer_money(to_account, amount)
            elif choice == "6":
                bank.get_transaction_history()
            elif choice == "7":
                bank.delete_account()
            elif choice == "8":
                bank.logout()
                return True  # Continue running but back to login screen
            else:
                print("Invalid choice. Please try again.")

if __name__ == "__main__":
    initialize_database()
    backup_database()  # Create initial backup
    
    bank = Bank()
    try:
        while True:
            if not main_menu(bank):
                break
    finally:
        bank.close_connection()
