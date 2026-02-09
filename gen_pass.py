import secrets
import string

def generate_password(length=32):
    alphabet = string.ascii_letters + string.digits #+ string.punctuation
    while True:
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)):
            # and any(c in string.punctuation for c in password)):
            return password

if __name__ == '__main__':
    print(generate_password())