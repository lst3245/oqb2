"""
Debug script to show what values are being read from .env file
"""
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Get values
host = os.getenv('DB_HOST', 'NOT_SET')
user = os.getenv('DB_USER', 'NOT_SET')
password = os.getenv('DB_PASSWORD', 'NOT_SET')
db_name = os.getenv('DB_NAME', 'NOT_SET')

print("=" * 60)
print("VALUES READ FROM .env FILE")
print("=" * 60)
print(f"DB_HOST     = '{host}'")
print(f"  Length: {len(host)}")
print(f"  Repr: {repr(host)}")
print()
print(f"DB_USER     = '{user}'")
print(f"  Length: {len(user)}")
print(f"  Repr: {repr(user)}")
print()
print(f"DB_PASSWORD = '{password}'")
print(f"  Length: {len(password)}")
print(f"  Repr: {repr(password)}")
print()
print(f"DB_NAME     = '{db_name}'")
print(f"  Length: {len(db_name)}")
print(f"  Repr: {repr(db_name)}")
print("=" * 60)

# Expected values
print("\nEXPECTED VALUES (from your hardcoded test):")
print("=" * 60)
print(f"DB_HOST     = '192.168.5.25'")
print(f"DB_USER     = 'root'")
print(f"DB_PASSWORD = 'FDh3MjT*qA'")
print(f"DB_NAME     = 'oqb2'")
print("=" * 60)

# Check for issues
print("\nISSUES DETECTED:")
print("=" * 60)
issues = []

if host != '192.168.5.25':
    issues.append(f"❌ DB_HOST doesn't match (got '{host}')")
if user != 'root':
    issues.append(f"❌ DB_USER doesn't match (got '{user}')")
if password != 'FDh3MjT*qA':
    issues.append(f"❌ DB_PASSWORD doesn't match (got '{password}')")
    # Check for common mistakes
    if password.startswith('"') or password.startswith("'"):
        issues.append("   → Password has quotes at the start!")
    if password.endswith('"') or password.endswith("'"):
        issues.append("   → Password has quotes at the end!")
    if ' ' in password:
        issues.append("   → Password contains spaces!")
if db_name != 'oqb2':
    issues.append(f"❌ DB_NAME doesn't match (got '{db_name}')")

if issues:
    for issue in issues:
        print(issue)
else:
    print("✅ All values match expected!")
print("=" * 60)