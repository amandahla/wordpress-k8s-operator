# Copyright 2022 Canonical Ltd.
# Licensed under the GPLv3, see LICENCE file for details.

import io
import json
import secrets
import socket
import tempfile
import unittest.mock
import urllib.parse

import ops.model
import PIL.Image
import pytest
import pytest_operator.plugin
import requests
import swiftclient
import swiftclient.exceptions
import swiftclient.service
from wordpress_client_for_test import WordpressClient

from charm import WordpressCharm


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: pytest_operator.plugin.OpsTest, application_name):
    """
    arrange: no pre-condition.
    act: build charm using charmcraft and deploy charm to test juju model.
    assert: building and deploying should success and status should be "blocked" since the
        database info hasn't been provided yet.
    """
    my_charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(
        my_charm,
        resources={"wordpress-image": "localhost:32000/wordpress:test"},
        application_name="wordpress",
        series="jammy",
    )
    await ops_test.model.wait_for_idle()
    for unit in ops_test.model.applications[application_name].units:
        assert (
            unit.workload_status == ops.model.BlockedStatus.name
        ), "status should be 'blocked' since the default database info is empty"

        assert (
            "Waiting for db" in unit.workload_status_message
        ), "status message should contain the reason why it's blocked"


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
@pytest.mark.parametrize(
    "app_config",
    [
        {
            "db_host": "test_db_host",
            "db_name": "test_db_name",
            "db_user": "test_db_user",
            "db_password": "test_db_password",
        }
    ],
    indirect=True,
    scope="function",
)
async def test_incorrect_db_config(
    ops_test: pytest_operator.plugin.OpsTest, app_config: dict, application_name
):
    """
    arrange: after WordPress charm has been deployed.
    act: provide incorrect database info via config.
    assert: charm should be blocked by WordPress installation errors, instead of lacking
        of database connection info.
    """
    # Database configuration can retry for up to 60 seconds before giving up and showing an error.
    # Default wait_for_idle 15 seconds in ``app_config`` fixture is too short for incorrect
    # db config.
    await ops_test.model.wait_for_idle(idle_period=60)

    for unit in ops_test.model.applications[application_name].units:
        assert (
            unit.workload_status == ops.model.BlockedStatus.name
        ), "unit status should be blocked"
        msg = unit.workload_status_message
        assert "MySQL error" in msg and (
            "2003" in msg or "2005" in msg
        ), "unit status message should show detailed installation failure"


@pytest.mark.asyncio
@pytest.mark.abort_on_fail
async def test_mysql_relation(ops_test: pytest_operator.plugin.OpsTest, application_name):
    """
    arrange: after WordPress charm has been deployed.
    act: deploy a mariadb charm and add a relation between WordPress and mariadb.
    assert: WordPress should be active.
    """
    await ops_test.model.deploy("charmed-osm-mariadb-k8s", application_name="mariadb")
    await ops_test.model.add_relation("wordpress", "mariadb:mysql")
    await ops_test.model.wait_for_idle()
    app_status = ops_test.model.applications[application_name].status
    assert app_status == ops.model.ActiveStatus.name, (
        "application status should be active once correct database connection info "
        "being provided via relation"
    )


@pytest.mark.asyncio
async def test_default_wordpress_themes_and_plugins(unit_ip_list, default_admin_password):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: test default installed themes and plugins.
    assert: default plugins and themes should match default themes and plugins defined in charm.py.
    """
    for unit_ip in unit_ip_list:
        wp = WordpressClient(
            host=unit_ip, username="admin", password=default_admin_password, is_admin=True
        )
        assert set(wp.list_themes()) == set(
            WordpressCharm._WORDPRESS_DEFAULT_THEMES
        ), "themes installed on WordPress should match default themes defined in charm.py"
        assert set(wp.list_plugins()) == set(
            WordpressCharm._WORDPRESS_DEFAULT_PLUGINS
        ), "plugins installed on WordPress should match default plugins defined in charm.py"


@pytest.mark.asyncio
async def test_wordpress_functionality(unit_ip_list, default_admin_password):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: test WordPress basic functionality (login, post, comment).
    assert: WordPress works normally as a blog site.
    """
    for unit_ip in unit_ip_list:
        WordpressClient.run_wordpress_functionality_test(
            host=unit_ip, admin_username="admin", admin_password=default_admin_password
        )


@pytest.mark.asyncio
async def test_wordpress_default_themes(unit_ip_list, get_theme_list_from_ip):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: check installed WordPress themes.
    assert: all default themes should be installed.
    """
    for unit_ip in unit_ip_list:
        assert set(WordpressCharm._WORDPRESS_DEFAULT_THEMES) == set(
            get_theme_list_from_ip(unit_ip)
        ), "default themes installed should match default themes defined in WordpressCharm"


@pytest.mark.asyncio
async def test_wordpress_install_uninstall_themes(
    ops_test: pytest_operator.plugin.OpsTest,
    application_name,
    unit_ip_list,
    get_theme_list_from_ip,
):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: change themes setting in config.
    assert: themes should be installed and uninstalled accordingly.
    """
    theme_change_list = [
        {"twentyfifteen", "classic"},
        {"tt1-blocks", "twentyfifteen"},
        {"tt1-blocks"},
        {"twentyeleven"},
        set(),
    ]
    for themes in theme_change_list:
        application = ops_test.model.applications[application_name]
        await application.set_config({"themes": ",".join(themes)})
        await ops_test.model.wait_for_idle()

        for unit_ip in unit_ip_list:
            expected_themes = themes
            expected_themes.update(WordpressCharm._WORDPRESS_DEFAULT_THEMES)
            assert expected_themes == set(
                get_theme_list_from_ip(unit_ip)
            ), f"theme installed {themes} should match themes setting in config"


@pytest.mark.asyncio
async def test_wordpress_theme_installation_error(
    ops_test: pytest_operator.plugin.OpsTest, application_name
):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: install a nonexistent theme.
    assert: charm should switch to blocked state and the reason should be included in the status
        message.
    """
    invalid_theme = "invalid-theme-sgkeahrgalejr"
    await ops_test.model.applications[application_name].set_config({"themes": invalid_theme})
    await ops_test.model.wait_for_idle()

    for unit in ops_test.model.applications[application_name].units:
        assert (
            unit.workload_status == ops.model.BlockedStatus.name
        ), "status should be 'blocked' since the theme in themes config does not exist"

        assert (
            invalid_theme in unit.workload_status_message
        ), "status message should contain the reason why it's blocked"

    await ops_test.model.applications[application_name].set_config({"themes": ""})
    await ops_test.model.wait_for_idle()

    for unit in ops_test.model.applications[application_name].units:
        assert (
            unit.workload_status == ops.model.ActiveStatus.name
        ), "status should back to active after invalid theme removed from config"


@pytest.mark.asyncio
async def test_wordpress_install_uninstall_plugins(
    ops_test: pytest_operator.plugin.OpsTest,
    application_name,
    unit_ip_list,
    get_plugin_list_from_ip,
):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: change plugins setting in config.
    assert: plugins should be installed and uninstalled accordingly.
    """
    plugin_change_list = [
        {"classic-editor", "classic-widgets"},
        {"classic-editor"},
        {"classic-widgets"},
        set(),
    ]
    for plugins in plugin_change_list:
        application = ops_test.model.applications[application_name]
        await application.set_config({"plugins": ",".join(plugins)})
        await ops_test.model.wait_for_idle()

        for unit_ip in unit_ip_list:
            expected_plugins = plugins
            expected_plugins.update(WordpressCharm._WORDPRESS_DEFAULT_PLUGINS)
            assert expected_plugins == set(
                get_plugin_list_from_ip(unit_ip)
            ), f"plugin installed {plugins} should match plugins setting in config"


@pytest.mark.asyncio
async def test_wordpress_plugin_installation_error(
    ops_test: pytest_operator.plugin.OpsTest, application_name
):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: install a nonexistent plugin.
    assert: charm should switch to blocked state and the reason should be included in the status
        message.
    """
    invalid_plugin = "invalid-plugin-sgkeahrgalejr"
    await ops_test.model.applications[application_name].set_config({"plugins": invalid_plugin})
    await ops_test.model.wait_for_idle()

    for unit in ops_test.model.applications[application_name].units:
        assert (
            unit.workload_status == ops.model.BlockedStatus.name
        ), "status should be 'blocked' since the plugin in plugins config does not exist"

        assert (
            invalid_plugin in unit.workload_status_message
        ), "status message should contain the reason why it's blocked"

    await ops_test.model.applications[application_name].set_config({"plugins": ""})
    await ops_test.model.wait_for_idle()

    for unit in ops_test.model.applications[application_name].units:
        assert (
            unit.workload_status == ops.model.ActiveStatus.name
        ), "status should back to active after invalid plugin removed from config"


@pytest.mark.asyncio
async def test_ingress(
    ops_test: pytest_operator.plugin.OpsTest, application_name: str, create_self_signed_tls_secret
):
    """
    arrange: after WordPress charm has been deployed and db relation established.
    act: deploy the nginx-ingress-integrator charm and create the relation between ingress charm
        and wordpress charm. After that, update some ingress related configuration of the
        wordpress charm.
    assert: A Kubernetes ingress should be created and the ingress should accept HTTPS connections
        after configuration tls_secret_name be set.
    """

    def gen_patch_getaddrinfo(host, resolve_to):
        original_getaddrinfo = socket.getaddrinfo

        def patched_getaddrinfo(*args):
            if args[0] == host:
                return original_getaddrinfo(resolve_to, *args[1:])
            else:
                return original_getaddrinfo(*args)

        return patched_getaddrinfo

    await ops_test.model.deploy("nginx-ingress-integrator", "ingress", trust=True)
    await ops_test.model.add_relation(application_name, "ingress:ingress")
    await ops_test.model.wait_for_idle(status=ops.model.ActiveStatus.name)

    response = requests.get("http://127.0.0.1", headers={"Host": application_name}, timeout=5)
    assert (
        response.status_code == 200 and "wordpress" in response.text.lower()
    ), "Ingress should accept requests to WordPress and return correct contents"

    tls_secret_name, tls_cert = create_self_signed_tls_secret(application_name)
    application = ops_test.model.applications[application_name]
    await application.set_config({"tls_secret_name": tls_secret_name})
    await ops_test.model.wait_for_idle(status=ops.model.ActiveStatus.name)

    with tempfile.NamedTemporaryFile(mode="wb+") as f:
        with unittest.mock.patch.multiple(
            socket, getaddrinfo=gen_patch_getaddrinfo(application_name, "127.0.0.1")
        ):
            f.write(tls_cert)
            f.flush()
            response = requests.get(f"https://{application_name}", verify=f.name, timeout=5)
            assert (
                response.status_code == 200 and "wordpress" in response.text.lower()
            ), "Ingress should accept HTTPS requests after tls_secret_name being set"

    new_hostname = "wordpress.test"
    tls_secret_name, tls_cert = create_self_signed_tls_secret(new_hostname)
    application = ops_test.model.applications[application_name]
    await application.set_config(
        {"tls_secret_name": tls_secret_name, "blog_hostname": new_hostname}
    )
    await ops_test.model.wait_for_idle(status=ops.model.ActiveStatus.name)

    with tempfile.NamedTemporaryFile(mode="wb+") as f:
        with unittest.mock.patch.multiple(
            socket, getaddrinfo=gen_patch_getaddrinfo(new_hostname, "127.0.0.1")
        ):
            f.write(tls_cert)
            f.flush()
            response = requests.get(f"https://{new_hostname}", verify=f.name, timeout=5)
            assert (
                response.status_code == 200 and "wordpress" in response.text.lower()
            ), "Ingress should update the server name indication based routing after blog_hostname updated"


@pytest.mark.asyncio
async def test_openstack_object_storage_plugin(
    ops_test: pytest_operator.plugin.OpsTest,
    application_name,
    default_admin_password,
    unit_ip_list,
    openstack_environment,
):
    """
    arrange: after charm deployed, db relation established and openstack swift server ready.
    act: update charm configuration for openstack object storage plugin.
    assert: openstack object storage plugin should be installed after the config update and
        WordPress openstack swift object storage integration should be set up properly.
        After openstack swift plugin activated, an image file uploaded to one unit through
        WordPress media uploader should be accessible from all units.
    """
    swift_conn = swiftclient.Connection(
        authurl=openstack_environment["OS_AUTH_URL"],
        auth_version="3",
        user=openstack_environment["OS_USERNAME"],
        key=openstack_environment["OS_PASSWORD"],
        os_options={
            "user_domain_name": openstack_environment["OS_USER_DOMAIN_ID"],
            "project_domain_name": openstack_environment["OS_PROJECT_DOMAIN_ID"],
            "project_name": openstack_environment["OS_PROJECT_NAME"],
        },
    )
    container_exists = True
    container = "WordPress"
    try:
        swift_conn.head_container(container)
    except swiftclient.exceptions.ClientException as e:
        if e.http_status == 404:
            container_exists = False
        else:
            raise e
    if container_exists:
        for swift_object in swift_conn.get_container(container, full_listing=True)[1]:
            swift_conn.delete_object(container, swift_object["name"])
        swift_conn.delete_container(container)
    swift_conn.put_container(container)
    swift_service = swiftclient.service.SwiftService(
        options=dict(
            auth_version="3",
            os_auth_url=openstack_environment["OS_AUTH_URL"],
            os_username=openstack_environment["OS_USERNAME"],
            os_password=openstack_environment["OS_PASSWORD"],
            os_project_name=openstack_environment["OS_PROJECT_NAME"],
            os_project_domain_name=openstack_environment["OS_PROJECT_DOMAIN_ID"],
        )
    )
    swift_service.post(container=container, options={"read_acl": ".r:*,.rlistings"})
    application = ops_test.model.applications[application_name]
    await application.set_config(
        {
            "wp_plugin_openstack-objectstorage_config": json.dumps(
                {
                    "auth-url": openstack_environment["OS_AUTH_URL"] + "/v3",
                    "bucket": container,
                    "password": openstack_environment["OS_PASSWORD"],
                    "object-prefix": "wp-content/uploads/",
                    "region": openstack_environment["OS_REGION_NAME"],
                    "tenant": openstack_environment["OS_PROJECT_NAME"],
                    "domain": openstack_environment["OS_PROJECT_DOMAIN_ID"],
                    "swift-url": swift_conn.url,
                    "username": openstack_environment["OS_USERNAME"],
                    "copy-to-swift": "1",
                    "serve-from-swift": "1",
                    "remove-local-file": "0",
                }
            )
        }
    )
    await ops_test.model.wait_for_idle()

    for idx, unit_ip in enumerate(unit_ip_list):
        image = PIL.Image.new("RGB", (500, 500), color=(idx, 0, 0))
        nonce = secrets.token_hex(8)
        filename = f"{nonce}.{unit_ip}.{idx}.jpg"
        image_buf = io.BytesIO()
        image.save(image_buf, format="jpeg")
        image = image_buf.getvalue()
        wp = WordpressClient(
            host=unit_ip, username="admin", password=default_admin_password, is_admin=True
        )
        image_urls = wp.upload_media(filename=filename, content=image)
        swift_object_list = [
            o["name"] for o in swift_conn.get_container(container, full_listing=True)[1]
        ]
        assert any(
            nonce in f for f in swift_object_list
        ), "media files uploaded should be stored in swift object storage"
        source_url = min(image_urls, key=len)
        for image_url in image_urls:
            assert (
                requests.get(image_url).status_code == 200
            ), "the original image and resized images should be accessible from the WordPress site"
        for host in unit_ip_list:
            url_components = list(urllib.parse.urlsplit(source_url))
            url_components[1] = host
            url = urllib.parse.urlunsplit(url_components)
            assert (
                requests.get(url).content == image
            ), "image downloaded from WordPress should match the image uploaded"


@pytest.mark.asyncio
async def test_openstack_akismet_plugin(
    ops_test: pytest_operator.plugin.OpsTest,
    application_name,
    default_admin_password,
    unit_ip_list,
    akismet_api_key,
):
    """
    arrange: after WordPress charm has been deployed, db relation established.
    act: update charm configuration for Akismet plugin.
    assert: Akismet plugin should be activated and spam detection function should be working.
    """
    application = ops_test.model.applications[application_name]
    await application.set_config({"wp_plugin_akismet_key": akismet_api_key})
    await ops_test.model.wait_for_idle()

    for unit_ip in unit_ip_list:
        wp = WordpressClient(
            host=unit_ip, username="admin", password=default_admin_password, is_admin=True
        )
        post = wp.create_post(secrets.token_hex(8), secrets.token_hex(8))
        wp.create_comment(
            post_id=post["id"], post_link=post["link"], content="akismet-guaranteed-spam"
        )
        wp.create_comment(post_id=post["id"], post_link=post["link"], content="test comment")
        assert (
            len(wp.list_comments(status="spam", post_id=post["id"])) == 1
        ), "Akismet plugin should move the triggered spam comment to the spam section"
        assert (
            len(wp.list_comments(post_id=post["id"])) == 1
        ), "Akismet plugin should keep the normal comment"


@pytest.mark.asyncio
async def test_openstack_openid_plugin(
    ops_test: pytest_operator.plugin.OpsTest,
    application_name,
    unit_ip_list,
    openid_username,
    openid_password,
    launchpad_team,
):
    """
    arrange: after WordPress charm has been deployed, db relation established.
    act: update charm configuration for OpenID plugin.
    assert: A WordPress user should be created with correct roles according to the config.
    """
    application = ops_test.model.applications[application_name]
    await application.set_config({"wp_plugin_openid_team_map": f"{launchpad_team}=administrator"})
    await ops_test.model.wait_for_idle()

    for idx, unit_ip in enumerate(unit_ip_list):
        # wordpress-teams-integration has a bug causing desired roles not to be assigned to
        # the user when first-time login. Login twice by creating the WordPressClient client twice
        # for the very first time.
        for _ in range(2 if idx == 0 else 1):
            wp = WordpressClient(
                host=unit_ip,
                username=openid_username,
                password=openid_password,
                is_admin=True,
                use_launchpad_login=True,
            )
        assert (
            "administrator" in wp.list_roles()
        ), "An launchpad OpenID account should be associated with the WordPress admin user"
