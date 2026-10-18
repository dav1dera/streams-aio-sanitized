# Commenti della configurazione YAML

Origine: `data/headscale/data/config/config.yaml`.

Documentazione dei commenti originali; il ripristino usa il template JSON adiacente.
I valori attivi della configurazione non sono inclusi in questo documento.
Blocchi omessi: 1 (materiale privato o esempi di credenziali).

```text
# headscale will look for a configuration file named `config.yaml` (or `config.json`) in the following order:
#
# - `/etc/headscale`
# - `~/.headscale`
# - current working directory

# The url clients will connect to.
# Typically this will be a domain like:
#
# https://myheadscale.example.com:443
#

# Address to listen to / bind to on the server
#
# For production:
# listen_addr: 0.0.0.0:8080

# Address to listen to /metrics and /debug, you may want
# to keep this endpoint private to your internal network

# Address to listen for gRPC.
# gRPC is used for controlling a headscale server
# remotely with the CLI
# Note: Remote access _only_ works if you have
# valid certificates.
#
# For production:
# grpc_listen_addr: 0.0.0.0:50443

# Allow the gRPC admin interface to run in INSECURE
# mode. This is not recommended as the traffic will
# be unencrypted. Only enable if you know what you
# are doing.

# The Noise section includes specific configuration for the
# TS2021 Noise protocol

  # The Noise private key is used to encrypt the traffic between headscale and
  # Tailscale clients when using the new Noise-based protocol. A missing key
  # will be automatically generated.

# List of IP prefixes to allocate tailaddresses from.
# Each prefix consists of either an IPv4 or IPv6 address,
# and the associated prefix length, delimited by a slash.
# It must be within IP ranges supported by the Tailscale
# client - i.e., subnets of 100.64.0.0/10 and fd7a:115c:a1e0::/48.
# See below:
# IPv6: https://github.com/tailscale/tailscale/blob/22ebb25e833264f58d7c3f534a8b166894a89536/net/tsaddr/tsaddr.go#LL81C52-L81C71
# IPv4: https://github.com/tailscale/tailscale/blob/22ebb25e833264f58d7c3f534a8b166894a89536/net/tsaddr/tsaddr.go#L33
# Any other range is NOT supported, and it will cause unexpected issues.

  # Strategy used for allocation of IPs to nodes, available options:
  # - sequential (default): assigns the next free IP from the previous given
  #   IP. A best-effort approach is used and Headscale might leave holes in the
  #   IP range or fill up existing holes in the IP range.
  # - random: assigns the next free IP from a pseudo-random IP generator (crypto/rand).

# DERP is a relay system that Tailscale uses when a direct
# connection cannot be established.
# https://tailscale.com/blog/how-tailscale-works/#encrypted-tcp-relays-derp
#
# headscale needs a list of DERP servers that can be presented
# to the clients.

    # If enabled, runs the embedded DERP server and merges it into the rest of the DERP config
    # The Headscale server_url defined above MUST be using https, DERP requires TLS to be in place

    # Region ID to use for the embedded DERP server.
    # The local DERP prevails if the region ID collides with other region ID coming from
    # the regular DERP config.

    # Region code and name are displayed in the Tailscale UI to identify a DERP region

    # Only allow clients associated with this server access

    # Listens over UDP at the configured address for STUN connections - to help with NAT traversal.
    # When the embedded DERP server is enabled stun_listen_addr MUST be defined.
    #
    # For more details on how this works, check this great article: https://tailscale.com/blog/how-tailscale-works/

    # Private key used to encrypt the traffic between headscale DERP and
    # Tailscale clients. A missing key will be automatically generated.

    # This flag can be used, so the DERP map entry for the embedded DERP server is not written automatically,
    # it enables the creation of your very own DERP map entry using a locally available file with the parameter DERP.paths
    # If you enable the DERP server and set this to false, it is required to add the DERP server to the DERP map using DERP.paths

    # For better connection stability (especially when using an Exit-Node and DNS is not working),
    # it is possible to optionally add the public IPv4 and IPv6 address to the Derp-Map using:

  # List of externally available DERP maps encoded in JSON

  # Locally available DERP map files encoded in YAML
  #
  # This option is mostly interesting for people hosting
  # their own DERP servers:
  # https://tailscale.com/kb/1118/custom-derp-servers/
  #
  # paths:
  #   - /etc/headscale/derp-example.yaml

  # If enabled, a worker will be set up to periodically
  # refresh the given sources and update the derpmap
  # will be set up.

  # How often should we check for DERP updates?

# Disables the automatic check for headscale updates on startup

# Time before an inactive ephemeral node is deleted?

  # Database type. Available options: sqlite, postgres
  # Please note that using Postgres is highly discouraged as it is only supported for legacy reasons.
  # All new development, testing and optimisations are done with SQLite in mind.

  # Enable debug mode. This setting requires the log.level to be set to "debug" or "trace".

  # GORM configuration settings.

    # Enable prepared statements.

    # Enable parameterized queries.

    # Skip logging "record not found" errors.

    # Threshold for slow queries in milliseconds.

  # SQLite config

    # Enable WAL mode for SQLite. This is recommended for production environments.
    # https://www.sqlite.org/wal.html

    # Maximum number of WAL file frames before the WAL file is automatically checkpointed.
    # https://www.sqlite.org/c3ref/wal_autocheckpoint.html
    # Set to 0 to disable automatic checkpointing.

  # # Postgres config
  # Please note that using Postgres is highly discouraged as it is only supported for legacy reasons.
  # See database.type for more information.
  # postgres:
  #   # If using a Unix socket to connect to Postgres, set the socket path in the 'host' field and leave 'port' blank.
  #   host: localhost
  #   port: 5432
  #   name: headscale
  #   user: foo
  #   pass: bar
  #   max_open_conns: 10
  #   max_idle_conns: 10
  #   conn_max_idle_time_secs: 3600

  #   # If other 'sslmode' is required instead of 'require(true)' and 'disabled(false)', set the 'sslmode' you need
  #   # in the 'ssl' field. Refers to https://www.postgresql.org/docs/current/libpq-ssl.html Table 34.1.
  #   ssl: false

  ### TLS configuration
  #
  ## Let's encrypt / ACME
  #
  # headscale supports automatically requesting and setting up
  # TLS for a domain with Let's Encrypt.
  #
  # URL to ACME directory

# Email to register with ACME provider

# Domain name to request a TLS certificate for:

# Path to store certificates and metadata needed by
# letsencrypt
# For production:

# Type of ACME challenge to use, currently supported types:
# HTTP-01 or TLS-ALPN-01
# See: docs/ref/tls.md for more information

# When HTTP-01 challenge is chosen, letsencrypt must set up a
# verification endpoint, and it will be listening on:
# :http = port 80

## Use already defined certificates:

  # Valid log levels: panic, fatal, error, warn, info, debug, trace

  # Output formatting for logs: text or json

## Policy
# headscale supports Tailscale's ACL policies.
# Please have a look to their KB to better
# understand the concepts: https://tailscale.com/kb/1018/acls/

  # The mode can be "file" or "database" that defines
  # where the ACL policies are stored and read from.

  # If the mode is set to "file", the path to a
  # HuJSON file containing ACL policies.

## DNS
#
# headscale supports Tailscale's DNS configuration and MagicDNS.
# Please have a look to their KB to better understand the concepts:
#
# - https://tailscale.com/kb/1054/dns/
# - https://tailscale.com/kb/1081/magicdns/
# - https://tailscale.com/blog/2021-09-private-dns-with-magicdns/
#
# Please note that for the DNS configuration to have any effect,
# clients must have the `--accept-dns=true` option enabled. This is the
# default for the Tailscale client. This option is enabled by default
# in the Tailscale client.
#
# Setting _any_ of the configuration and `--accept-dns=true` on the
# clients will integrate with the DNS manager on the client or
# overwrite /etc/resolv.conf.
# https://tailscale.com/kb/1235/resolv-conf
#
# If you want stop Headscale from managing the DNS configuration
# all the fields under `dns` should be set to empty values.

  # Whether to use [MagicDNS](https://tailscale.com/kb/1081/magicdns/).

  # Defines the base domain to create the hostnames for MagicDNS.
  # This domain _must_ be different from the server_url domain.
  # `base_domain` must be a FQDN, without the trailing dot.
  # The FQDN of the hosts will be
  # `hostname.base_domain` (e.g., _myhost.example.com_).

  # Whether to use the local DNS settings of a node or override the local DNS
  # settings (default) and force the use of Headscale's DNS configuration.

  # List of DNS servers to expose to clients.

      # foo.bar.com:
      #   - 1.1.1.1
      # darp.headscale.net:
      #   - 1.1.1.1
      #   - 8.8.8.8

  # Set custom DNS search domains. With MagicDNS enabled,
  # your tailnet base_domain is always the first search domain.

  # Extra DNS records
  # so far only A and AAAA records are supported (on the tailscale side)
  # See: docs/ref/dns.md

  #   - name: "grafana.myvpn.example.com"
  #     type: "A"
  #     value: "100.64.0.3"
  #
  #   # you can also put it in one line
  #   - { name: "prometheus.myvpn.example.com", type: "A", value: "100.64.0.3" }
  #
  # Alternatively, extra DNS records can be loaded from a JSON file.
  # Headscale processes this file on each change.
  # extra_records_path: /var/lib/headscale/extra-records.json

  # Unix socket used for the CLI to connect without authentication
  # Note: for production you will want to set this to something like:

  # Enable logtail for tailscale nodes of this Headscale instance.
  # As there is currently no support for overriding the log server in Headscale, this is
  # disabled by default. Enabling this will make your clients send logs to Tailscale Inc.

# Enabling this option makes devices prefer a random port for WireGuard traffic over the
# default static port 41641. This option is intended as a workaround for some buggy
# firewall devices. See https://tailscale.com/kb/1181/firewalls/ for more information.
# randomize_client_port: false
```
