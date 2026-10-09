#!/bin/sh
# Creates (once) a private certificate authority and a server certificate for the web UI.
#
# The CA is name-constrained: it can only sign *.local, localhost and private IP ranges,
# so even if its key leaked it could not be used to impersonate real websites.
# Devices trust the app by installing ca.crt (served at /dam-ca.crt). The server certificate
# is re-issued automatically when TLS_HOSTNAMES / TLS_IPS change or it nears expiry.
set -eu

DIR=/etc/dam-tls
NAMES="${TLS_HOSTNAMES:-dam.local localhost}"
IPS="${TLS_IPS:-127.0.0.1}"
mkdir -p "$DIR"
chmod 700 "$DIR"
umask 077

if [ ! -s "$DIR/ca.key" ] || [ ! -s "$DIR/ca.crt" ]; then
    echo "dam-tls: creating private certificate authority"
    cat > "$DIR/ca.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ca_ext
prompt = no
[dn]
CN = DAM Local CA ($(hostname))
O = Digital Asset Manager
[ca_ext]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, permitted;DNS:.local, permitted;DNS:localhost, permitted;IP:10.0.0.0/255.0.0.0, permitted;IP:172.16.0.0/255.240.0.0, permitted;IP:192.168.0.0/255.255.0.0, permitted;IP:127.0.0.0/255.0.0.0
CNF
    openssl req -x509 -new -newkey rsa:3072 -nodes -days 3650 \
        -keyout "$DIR/ca.key" -out "$DIR/ca.crt" -config "$DIR/ca.cnf"
    rm -f "$DIR/server.crt"
fi

SANS=""
for n in $NAMES; do SANS="${SANS:+$SANS,}DNS:$n"; done
for i in $IPS; do SANS="${SANS:+$SANS,}IP:$i"; done

reissue=1
if [ -s "$DIR/server.crt" ] && [ -s "$DIR/server.key" ] && [ "$(cat "$DIR/server.sans" 2>/dev/null)" = "$SANS" ] \
   && openssl x509 -checkend 2592000 -noout -in "$DIR/server.crt" >/dev/null; then
    reissue=0
fi

if [ "$reissue" = 1 ]; then
    echo "dam-tls: issuing server certificate for $SANS"
    cat > "$DIR/server.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = $(echo $NAMES | cut -d' ' -f1)
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = $SANS
CNF
    openssl req -new -newkey rsa:2048 -nodes -keyout "$DIR/server.key" -out "$DIR/server.csr" -config "$DIR/server.cnf"
    # 397 days: the longest validity Apple devices accept for TLS server certificates
    openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial \
        -days 397 -sha256 -extfile "$DIR/server.cnf" -extensions ext -out "$DIR/server.crt"
    cat "$DIR/server.crt" "$DIR/ca.crt" > "$DIR/fullchain.crt"
    echo "$SANS" > "$DIR/server.sans"
    rm -f "$DIR/server.csr"
fi

# Public: what devices download to trust the app (never the key).
umask 022
mkdir -p /usr/share/nginx/public
chmod 755 /usr/share/nginx/public
cp "$DIR/ca.crt" /usr/share/nginx/public/dam-ca.crt
chmod 644 /usr/share/nginx/public/dam-ca.crt
