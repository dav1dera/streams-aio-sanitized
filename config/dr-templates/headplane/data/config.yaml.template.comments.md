# Commenti della configurazione YAML

Origine: `data/headplane/data/config.yaml`.

Documentazione dei commenti originali; il ripristino usa il template JSON adiacente.
I valori attivi della configurazione non sono inclusi in questo documento.
Blocchi omessi: 4 (materiale privato o esempi di credenziali).

```text
# Configuration for the Headplane server and web application

  # These are the default values, change them as needed

  # The base URL for Headplane. Please keep in mind that this will be required
  # for Headscale to properly function going forward AND it should not include
  # the dashboard prefix (/admin) portion.

  # The secret used to encode and decode web sessions (must be 32 characters)
  # You may also provide `cookie_secret_path` instead to read a value from disk.
  # See https://headplane.net/configuration/#sensitive-values

  # Whether cookies should be marked as Secure
  # * Should be false if running without HTTPs
  # * Should be true if running behind a reverse proxy with HTTPs

# Headscale specific settings to allow Headplane to talk
# to Headscale and access deep integration features

  # The URL to your Headscale instance
  # (All API requests are routed through this URL)
  # (THIS IS NOT the gRPC endpoint, but the HTTP endpoint)
  #
  # IMPORTANT: If you are using TLS this MUST be set to `https://`

  # If you use the TLS configuration in Headscale, and you are not using
  # Let's Encrypt for your certificate, pass in the path to the certificate.
  # (This has no effect if `url` does not start with `https://`)
  # tls_cert_path: "/var/lib/headplane/tls.crt"

  # Optional, public URL if its different from the `headscale.url`
  # This affects certain parts of the web UI which shows Headscale's URL
  # public_url: "https://headscale.example.com"

  # Path to the Headscale configuration file
  # This is optional, but HIGHLY recommended for the best experience
  # If this is read only, Headplane will show your configuration settings
  # in the Web UI, but they cannot be changed.

  # Whether the Headscale configuration should be strictly validated
  # when reading from `config_path`. If true, Headplane will not interact
  # with Headscale if there are any issues with the configuration file.
  #
  # This is recommended to be true for production deployments to, however it
  # may not work if you are using a version of Headscale that has configuration
  # options unknown to Headplane.

  # If you are using `dns.extra_records_path` in your Headscale
  # configuration, you need to set this to the path for Headplane
  # to be able to read the DNS records.
  #
  # Pass it in if using Docker and ensure that the file is both
  # readable and writable to the Headplane process.
  # When using this, Headplane will no longer need to automatically
  # restart Headscale for DNS record changes.
  # dns_records_path: "/var/lib/headscale/extra_records.json"

# Integration configurations for Headplane to interact with Headscale

  # The Headplane agent allows retrieving information about nodes
  # This allows the UI to display version, OS, and connectivity data
  # You will see the Headplane agent in your Tailnet as a node when
  # it connects.

    # To connect to your Tailnet, you need to generate a pre-auth key
    # This can be done via the web UI or through the `headscale` CLI.

    # Optionally change the name of the agent in the Tailnet.

    # Configure different caching settings. By default, the agent will store
    # caches in the path below for a maximum of 1 minute. If you want data
    # to update faster, reduce the TTL, but this will increase the frequency
    # of requests to Headscale.
    # cache_ttl: 60
    # cache_path: /var/lib/headplane/agent_cache.json

    # The work_dir represents where the agent will store its data to be able
    # to automatically reauthenticate with your Tailnet. It needs to be
    # writable by the user running the Headplane process.
    #
    # If using Docker, it is best to leave this as the default.
    # work_dir: "/var/lib/headplane/agent"

  # Only one of these should be enabled at a time or you will get errors
  # This does not include the agent integration (above), which can be enabled
  # at the same time as any of these and is recommended for the best experience.

    # By default we check for the presence of a container label (see the docs)
    # to determine the container to signal when changes are made to DNS settings.

    # HOWEVER, you can fallback to a container name if you desire, but this is
    # not recommended as its brittle and doesn't work with orchestrators that
    # automatically assign container names.
    #
    # If `container_name` is set, it will override any label checks.
    # container_name: "headscale"

    # The path to the Docker socket (do not change this if you are unsure)
    # Docker socket paths must start with unix:// or tcp:// and at the moment
    # https connections are not supported.

  # Please refer to docs/integration/Kubernetes.md for more information
  # on how to configure the Kubernetes integration. There are requirements in
  # order to allow Headscale to be controlled by Headplane in a cluster.

    # Validates the manifest for the Pod to ensure all of the criteria
    # are set correctly. Turn this off if you are having issues with
    # shareProcessNamespace not being validated correctly.

    # This should be the name of the Pod running Headscale and Headplane.
    # If this isn't static you should be using the Kubernetes Downward API
    # to set this value (refer to docs/Integrated-Mode.md for more info).

  # Proc is the "Native" integration that only works when Headscale and
  # Headplane are running outside of a container. There is no configuration,
  # but you need to ensure that the Headplane process can terminate the
  # Headscale process.
  #
  # (If they are both running under systemd as sudo, this will work).

# OIDC Configuration for simpler authentication
# (This is optional, but recommended for the best experience)
```
