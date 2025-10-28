# ssl-proxy

This docker compose file sets up a SSL terminated reverse proxy.

## setup

To run this proxy:

```
docker compose up
```

Then any containers that join the "nginx-proxy" network and EXPOSE a network
port, that port will be exposed in the reverse proxy. To enable this, make
sure the container to be exposed has the following environment variables:

```
    environment:
      - VIRTUAL_HOST=<fully qualified domain to be used>
      - LETSENCRYPT_HOST=<same domain as above>
      - LETSENCRYPT_EMAIL=<email where you can be reached by Let's Encrypt>
```
