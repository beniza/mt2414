import os
import uuid
import sqlite3
import json
import psycopg2
from functools import wraps
from datetime import datetime, timedelta
from xlwt import Workbook
import scrypt
import requests
import jwt
from flask import Flask, request, session
from flask import g
from flask_cors import CORS, cross_origin
import nltk
import polib
import re
import base64


PO_METADATA = {
    'Project-Id-Version': '1.0',
    'Report-Msgid-Bugs-To': 'tfbfgroup@googlegroups.com',
    'POT-Creation-Date': '2007-10-18 14:00+0100',
    'PO-Revision-Date': '2007-10-18 14:00+0100',
    'Last-Translator': 'you <you@example.com>',
    'Language-Team': 'English <yourteam@example.com>',
    'MIME-Version': '1.0',
    'Content-Type': 'text/plain; charset=utf-8',
    'Content-Transfer-Encoding': '8bit',
}


app = Flask(__name__)
CORS(app)

sendinblue_key = os.environ.get("MT2414_SENDINBLUE_KEY")
jwt_hs256_secret = os.environ.get("MT2414_HS256_SECRET")
postgres_host = os.environ.get("MT2414_POSTGRES_HOST", "localhost")
postgres_port = os.environ.get("MT2414_POSTGRES_PORT", "5432")
postgres_user = os.environ.get("MT2414_POSTGRES_USER", "postgres")
postgres_password = os.environ.get("MT2414_POSTGRES_PASSWORD", "secret")
postgres_database = os.environ.get("MT2414_POSTGRES_DATABASE", "postgres")

def get_db():
    """Opens a new database connection if there is none yet for the
    current application context.
    """
    if not hasattr(g, 'db'):
        g.db = psycopg2.connect(dbname=postgres_database, user=postgres_user, password=postgres_password, host=postgres_host, port=postgres_port)
    return g.db


@app.teardown_appcontext
def close_db(error):
    """Closes the database again at the end of the request."""
    if hasattr(g, 'db'):
        g.db.close()


@app.route("/v1/auth", methods=["POST"])
def auth():
    email = request.form["username"]
    password = request.form["password"]
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT email FROM users WHERE  email = %s",(email,))
    est = cursor.fetchone()
    if not est:
        return '{success:false, message:"Invalid email"}'
    cursor.execute("SELECT password_hash, password_salt FROM users WHERE email = %s AND email_verified = True", (email,))
    rst = cursor.fetchone()
    if not rst:
        return '{success:false, message:"Email is not Verified"}'
    password_hash = rst[0].hex()
    password_salt = bytes.fromhex(rst[1].hex())
    password_hash_new = scrypt.hash(password, password_salt).hex()
    if password_hash == password_hash_new:
        access_token = jwt.encode({'sub': email}, jwt_hs256_secret, algorithm='HS256')
        return '{"access_token": "%s"}\n' % access_token.decode('utf-8')
    return '{success:false, message:"Incorrect Password"}'


@app.route("/v1/registrations", methods=["POST"])
def new_registration():
    email = request.form['email']
    password = request.form['password']
    headers = {"api-key": sendinblue_key}
    url = "https://api.sendinblue.com/v2.0/email"
    verification_code = str(uuid.uuid4()).replace("-","")
    body = '''Hi,<br/><br/>Thanks for your interest to use the MT2414 web service. <br/>
    You need to confirm your email by opening this link:

    <a href="https://api.mt2414.in/v1/verifications/%s">https://api.mt2414.in/v1/verifications/%s</a>

    <br/><br/>The documentation for accessing the API is available at <a href="http://docs.mt2414.in">docs.mt2414.in</a>''' % (verification_code, verification_code)
    payload = {
        "to": {email: ""},
        "from": ["noreply@mt2414.in","Mt. 24:14"],
        "subject": "MT2414 - Please verify your email address",
        "html": body,
        }
    connection = get_db()
    password_salt = str(uuid.uuid4()).replace("-","")
    password_hash = scrypt.hash(password, password_salt)


    cursor = connection.cursor()
    cursor.execute("SELECT email FROM users WHERE email = %s", (email,))
    if cursor.fetchone():
        return '{success:false, message:"Email Already Exists"}'
    else:
        cursor.execute("INSERT INTO users (email, verification_code, password_hash, password_salt, created_at) VALUES (%s, %s, %s, %s, current_timestamp)",
                (email, verification_code, password_hash, password_salt))
    cursor.close()
    connection.commit()
    resp = requests.post(url, data=json.dumps(payload), headers=headers)
    return '{success:true, message:"Verification Email Sent"}'

@app.route("/v1/resetpassword", methods = ["POST"])
def reset_password():
    email = request.form['email']
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT email from users WHERE email = %s", (email,))
    if cursor.fetchone():
        headers = {"api-key": sendinblue_key}
        url = "https://api.sendinblue.com/v2.0/email"
        verification_code = str(uuid.uuid4()).replace("-","")
        body = '''Hi,<br/><br/>your request for resetting the password has been recieved. <br/>
        Enter your new password by opening this link:

        <a href="https://api.mt2414.in/v1/forgotpassword/%s">https://api.mt2414.in/v1/forgotpassword/%s</a>

        <br/><br/>The documentation for accessing the API is available at <a href="http://docs.mt2414.in">docs.mt2414.in</a>''' % (verification_code, verification_code)
        payload = {
            "to": {email: ""},
            "from": ["noreply@mt2414.in","Mt. 24:14"],
            "subject": "MT2414 - Password reset verification mail",
            "html": body,
            }
        cursor.execute("UPDATE users SET verification_code= %s WHERE email = %s", (verification_code, email))
        cursor.close()
        connection.commit()
        resp = requests.post(url, data=json.dumps(payload), headers=headers)
    else:
        return '{success:false, message:"Email has not yet been registered"}'
    return '{success:true, message:"Link to reset password has been sent to the registered mail ID"}\n'

@app.route("/v1/forgotpassword/<string:code>", methods = ["POST"])
def reset_password2(code):
    password = request.form['password']
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT email FROM users WHERE verification_code = %s AND email_verified = True", (code,))
    rst = cursor.fetchone()
    email = rst[0]
    password_salt = str(uuid.uuid4()).replace("-","")
    password_hash = scrypt.hash(password, password_salt)
    cursor.execute("UPDATE users SET verification_code = %s, password_hash = %s, password_salt = %s, created_at = current_timestamp WHERE email = %s", (code, password_hash, password_salt, email))
    cursor.close()
    connection.commit()
    return '{success:true, message:"Password has been reset"}\n'

class TokenError(Exception):

    def __init__(self, error, description, status_code=401, headers=None):
        self.error = error
        self.description = description
        self.status_code = status_code
        self.headers = headers

    def __repr__(self):
        return 'TokenError: %s' % self.error

    def __str__(self):
        return '%s. %s' % (self.error, self.description)

@app.errorhandler(TokenError)
def auth_exception_handler(error):
    return 'Authentication Failed\n', 401

def check_token(f):
    @wraps(f)
    def wrapper(*args, **kwds):
        auth_header_value = request.headers.get('Authorization', None)
        if not auth_header_value:
            raise TokenError('No Authorization header', 'Token missing')

        parts = auth_header_value.split()

        if (len(parts) == 1) and (parts[0].lower() != 'bearer'):
            access_id, key = parts[0].split(":")
            connection = get_db()
            cursor = connection.cursor()
            cursor.execute("SELECT keys.key_hash, keys.key_salt, users.email FROM keys LEFT JOIN users ON keys.user_id = users.id WHERE keys.access_id = %s AND users.email_verified = True", (access_id,))
            rst = cursor.fetchone()
            if not rst:
                raise TokenError('Invalid token', 'Invalid token')
            key_hash = rst[0].hex()
            key_salt = bytes.fromhex(rst[1].hex())

            key_hash_new = scrypt.hash(key, key_salt).hex()
            if key_hash == key_hash_new:
                request.email = rst[2]
            else:
                raise TokenError('Invalid token', 'Invalid token')
        elif (len(parts) == 2) and (parts[0].lower() == 'bearer'):
            # check for JWT token
            token = parts[1]
            options = {
                'verify_sub': True
            }
            algorithm = 'HS256'
            leeway = timedelta(seconds=10)

            try:
                decoded = jwt.decode(token, jwt_hs256_secret, options=options, algorithms=[algorithm], leeway=leeway)
                request.email = decoded['sub']
            except jwt.exceptions.DecodeError as e:
                raise TokenError('Invalid token', str(e))
        else:
            raise TokenError('Invalid header', 'Token contains spaces')

        #raise TokenError('Invalid JWT header', 'Token missing')
        return f(*args, **kwds)
    return wrapper

@app.route("/v1/keys", methods=["POST"])
@check_token
def new_key():
    key = str(uuid.uuid4()).replace("-","")
    access_id = str(uuid.uuid4()).replace("-","")
    key_salt = str(uuid.uuid4()).replace("-","")
    key_hash = scrypt.hash(key, key_salt)

    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM keys LEFT JOIN users ON keys.user_id = users.id WHERE users.email = %s AND users.email_verified = True", (request.email,))
    rst = cursor.fetchone()
    cursor.execute("SELECT id FROM users WHERE email = %s", (request.email,))
    rst2 = cursor.fetchone()
    user_id = rst2[0]
    if rst:
        cursor.execute("UPDATE keys SET access_id=%s, key_hash=%s, key_salt=%s WHERE user_id=%s", (access_id, key_hash, key_salt, user_id))
    else:
        cursor.execute("INSERT INTO keys (access_id, key_hash, key_salt, user_id) VALUES (%s, %s, %s, %s)", (access_id, key_hash, key_salt, user_id))
    cursor.close()
    connection.commit()
    return '{"id": "%s", "key": "%s"}\n' % (access_id, key)

@app.route("/v1/verifications/<string:code>", methods=["GET"])
def new_registration2(code):
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT email FROM users WHERE verification_code = %s AND email_verified = False", (code,))
    if cursor.fetchone():
        cursor.execute("UPDATE users SET email_verified = True WHERE verification_code = %s", (code,))
    cursor.close()
    connection.commit()
    return '{success:true, message:"Email Verified"}'


@app.route("/v1/sources", methods=["POST"])
@check_token
def sources():
    req = request.get_json(True)
    language = req["language"]
    content = req["content"]
    version = req["version"]
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT id from sources WHERE language = %s and version = %s",(language, version))
    try:
        rst = cursor.fetchone()
    except:
        pass
    cursor.close()
    if rst:
        cursor = connection.cursor()
        source_id = rst[0]
        books = []
        cursor.execute("SELECT book_name, content, revision_num from sourcetexts WHERE source_id = %s", (source_id,))
        all_books = cursor.fetchall()
        for i in range(0, len(all_books)):
            books.append(all_books[i][0])
        for files in content:
            text_file = ((base64.b64decode(files)).decode('utf-8')).replace('\r','')
            book_name = (re.search('(?<=\id )\w+', text_file)).group(0)
            if book_name in books:
                count = 0
                for i in range(0, len(all_books)):
                    if all_books[i][1] != text_file and book_name == all_books[i][0]:
                        count = count + 1
                revision_num = count + 1
                cursor.execute("INSERT INTO sourcetexts (book_name, content, source_id, revision_num) VALUES (%s, %s, %s, %s)", (book_name, text_file, source_id, revision_num))
            elif book_name not in books:
                revision_num = 1
                cursor.execute("INSERT INTO sourcetexts (book_name, content, source_id, revision_num) VALUES (%s, %s, %s, %s)", (book_name, text_file, source_id, revision_num))
        cursor.close()
        connection.commit()
        return "sources updated"
    else:
        cursor = connection.cursor()
        cursor.execute("INSERT INTO sources (language, version) VALUES (%s , %s) RETURNING id", (language, version))
        source_id = cursor.fetchone()[0]
        for files in content:
            text_file = ((base64.b64decode(files)).decode('utf-8')).replace('\r','')
            book_name = (re.search('(?<=\id )\w+', text_file)).group(0)
            revision_num = 1
            cursor.execute("INSERT INTO sourcetexts (book_name, content, revision_num, source_id) VALUES (%s, %s, %s, %s)", (book_name, text_file, revision_num, source_id))
            cursor.close()
            connection.commit()
        return "New sources created"

@app.route("/v1/get_languages", methods=["POST"])
@check_token
def availableslan():
    connection =get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT language FROM sources")
    l=cursor.fetchall()
    #for lst in l:
    return json.dumps(str(l))
    cursor.close()



@app.route("/v1/tokenwords", methods=["GET", "POST"])
@check_token
def tokenwords():
    req = request.get_json(True)
    language = req["language"]
    version = req["version"]
    revision = req["revision"]
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT id from sources WHERE language = %s AND version = %s", (language, version))
    source_id = cursor.fetchone()[0]
    cursor.execute("SELECT content from sourcetexts WHERE source_id = %s AND revision_num = %s", (source_id, revision))
    out = []
    for rst in cursor.fetchall():
        out.append(rst[0])
    remove_punct = re.sub(r'([!"#$%&\'\(\)\*\+,-\.\/:;<=>\?\@\[\]^_`{|\}~।])','', (" ".join(out)))
    token_list = nltk.word_tokenize(remove_punct)
    token_set = set([x.encode('utf-8') for x in token_list])
    words = []
    for t in token_set:
        entry = {
                "msgid": t.decode("utf-8"),
                "msgstr": '',
                }
        words.append(entry)
        cursor.execute("SELECT token FROM tokenwords WHERE token = %s AND source_id = %s AND revision_num = %s", (t.decode("utf-8"), source_id, revision))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO tokenwords (token, revision_num, source_id) VALUES (%s, %s, %s)", (t.decode("utf-8"), revision, source_id))
    cursor.close()
    connection.commit()
    tw = {}
    tw["tokenwords"] = str(words)
    return json.dumps(tw)


@app.route("/v1/translations", methods=["POST"])
@check_token
def translations():
    req = request.get_json(True)
    sourcelang = req["sourcelang"]
    #targetlang = req["targetlang"]
    tokens = req["tokenwords"]
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("select st.name, st.content, st.source_id from sourcetexts st left join sources s on st.source_id = s.id WHERE s.language = %s", (sourcelang,))
    out = []
    for rst in cursor.fetchall():
        out.append((rst[0], rst[1]))
    source_id = rst[2]
    tr = {}
    for name, book in out:
        out_text_lines = []
        for line in book.split("\n"):
            line_words = nltk.word_tokenize(line)#.decode('utf8'))
            new_line_words = []
            for word in line_words:
                new_line_words.append(tokens.get(word, word))
            out_line = " ".join(new_line_words)
            out_text_lines.append(out_line)

        out_text = "\n".join(out_text_lines)
        tr[name] = out_text
        cursor.execute("INSERT INTO translationtexts (name, content, language, source_id) VALUES (%s, %s, %s, %s)", (name, out_text, sourcelang, source_id))
        cursor.close()
        connection.commit()
    return json.dumps(tr)

@app.route("/v1/corrections", methods=["POST"])
@check_token
def corrections():
    return '{}\n'


@app.route("/v1/suggestions", methods=["GET"])
@check_token
def suggestions():
    return '{}\n'
