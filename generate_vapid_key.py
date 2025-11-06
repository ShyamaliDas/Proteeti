# generate_vapid_key.py
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64
import json

# Generate private key
private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
public_key = private_key.public_key()

# Get the raw bytes for private key (d value)
private_numbers = private_key.private_numbers()
private_key_bytes = private_numbers.private_value.to_bytes(32, byteorder='big')

# Get the raw bytes for public key (x and y coordinates)
public_numbers = public_key.public_numbers()
public_x = public_numbers.x.to_bytes(32, byteorder='big')
public_y = public_numbers.y.to_bytes(32, byteorder='big')
public_key_bytes = public_x + public_y

# Convert to URL-safe base64 without padding
private_key_b64 = base64.urlsafe_b64encode(private_key_bytes).decode().rstrip('=')
public_key_b64 = base64.urlsafe_b64encode(public_key_bytes).decode().rstrip('=')

print("=== VAPID Keys ===")
print(f"\nPublic Key (for browser):\n{public_key_b64}")
print(f"\nPrivate Key (keep secret):\n{private_key_b64}")

# Save to file
keys = {
    "public_key": public_key_b64,
    "private_key": private_key_b64
}

with open('vapid_keys.json', 'w') as f:
    json.dump(keys, f, indent=2)

print("\n✓ Keys saved to vapid_keys.json")
