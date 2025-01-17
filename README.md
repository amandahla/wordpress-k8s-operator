# WordPress Operator

A Juju charm for a Kubernetes deployment of WordPress, configurable to use a
MySQL backend.

## Overview

WordPress powers more than 39% of the web — a figure that rises every day.
Everything from simple websites, to blogs, to complex portals and enterprise
websites, and even applications, are built with WordPress. WordPress combines
simplicity for users and publishers with under-the-hood complexity for
developers. This makes it flexible while still being easy-to-use.

## Usage

For details on using Kubernetes with Juju [see here](https://juju.is/docs/kubernetes), and for
details on using Juju with MicroK8s for easy local testing [see here](https://juju.is/docs/microk8s-cloud).

To deploy the charm and relate it to the [MariaDB K8s charm](https://charmhub.io/mariadb) within a Juju
Kubernetes model:

    juju deploy nginx-ingress-integrator ingress
    juju deploy charmed-osm-mariadb-k8s mariadb
    juju deploy wordpress-k8s --resource wordpress-image=wordpresscharmers/wordpress:bionic-5.7
    juju relate wordpress-k8s mariadb:mysql
    juju relate wordpress-k8s ingress:ingress

It will take about 2 to 5 minutes for Juju hooks to discover the site is live
and perform the initial setup for you. Once the "Workload" status is "active",
your WordPress site is configured.

To retrieve the auto-generated admin password, run the following:

    juju run-action --wait wordpress-k8s/0 get-initial-password

You should now be able to browse to the site hostname. Here's some
sample output from `juju status`:

    Unit                Workload     Agent  Address      Ports     Message
    mariadb/0*          active       idle   10.1.234.43  3306/TCP  ready
    wordpress-k8s/0*    active       idle   10.1.234.13  80/TCP    Pod configured

In this case our `UNIT_IP` is 10.1.234.13. If we visit `http://${UNIT_IP}/`
you'll see the WordPress site itself, or you can log in to the admin site
at `http://{$UNIT_IP}/wp-admin` using a username of `admin` and the password
from the `get-initial-password` action above.

For further details, [see here](https://charmhub.io/wordpress-k8s/docs).
