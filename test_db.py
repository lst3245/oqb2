import os
import pymysql
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

host = os.getenv('DB_HOST', 'localhost')
user = os.getenv('DB_USER', 'root')
password = os.getenv('DB_PASSWORD', '')
db_name = os.getenv('DB_NAME', 'oqb2')

print(f"--- Database Connection Test ---")
print(f"Target: {user}@{host}")
print(f"Database: {db_name}")

try:
    # 1. Test Server Connection
    print("\nAttempting to connect to MariaDB server...")
    conn = pymysql.connect(
        host="192.168.5.25",
        user="root",
        password="FDh3MjT*qA"
    )
    print("✅ SUCCESS: Credentials are correct! Connected to server.")
    
    # 2. Test Database Existence
    try:
        print(f"Checking for database '{db_name}'...")
        conn.select_db(db_name)
        print(f"✅ SUCCESS: Database '{db_name}' exists and is accessible.")
    except pymysql.err.OperationalError as e:
        code, msg = e.args
        if code == 1049: # Unknown database
            print(f"❌ ERROR: Database '{db_name}' does not exist.")
            print("   Please log in to MariaDB and run: CREATE DATABASE oqb2;")
        else:
            print(f"❌ ERROR: Could not select database. {msg}")
            
    conn.close()

except pymysql.err.OperationalError as e:
    code, msg = e.args
    print(f"❌ CONNECTION FAILED: {msg}")
    if code == 1045:
        print("\nPossible solutions:")
        print("1. Check DB_PASSWORD in your .env file")
        print("2. Check if DB_USER is correct (default: root)")
        print("3. Try setting DB_HOST=127.0.0.1 in .env if localhost fails")
    elif code == 2003:
        print("\nPossible solutions:")
        print("1. Ensure MariaDB service is running")
        print("2. Check DB_HOST in .env (try 127.0.0.1)")

except Exception as e:
    print(f"❌ UNEXPECTED ERROR: {str(e)}")
