# -*- coding: utf-8 -*-
import fauxfactory
import pytest
from riggerlib import recursive_update

from cfme import test_requirements
from cfme.configure.configuration.region_settings import Category, Tag
from cfme.cloud.instance import Instance
from cfme.cloud.provider.gce import GCEProvider
from cfme.cloud.provider.openstack import OpenStackProvider
from cfme.services.service_catalogs import ServiceCatalogs
from cfme.utils.appliance import ViaSSUI, ViaUI
from cfme.utils.generators import random_vm_name
from cfme.utils.update import update

from widgetastic.utils import partial_match

pytestmark = [
    test_requirements.quota,
    pytest.mark.provider([GCEProvider, OpenStackProvider],
                         required_fields=[['provisioning', 'image']], scope="module")
]


@pytest.fixture
def admin_email(appliance):
    """Required for user quota tagging services to work, as it's mandatory for it's functioning."""
    user = appliance.collections.users
    admin = user.instantiate(name='Administrator')
    with update(admin):
        admin.email = 'xyz@redhat.com'
    yield
    with update(admin):
        admin.email = ''


@pytest.fixture
def vm_name():
    return random_vm_name(context='quota')


@pytest.fixture
def template_name(provisioning):
    return provisioning["image"]["name"]


@pytest.fixture
def prov_data(provider, vm_name, template_name):
    if provider.one_of(OpenStackProvider):
        return {
            "catalog": {'vm_name': vm_name, 'catalog_name': {'name': template_name}},
            "environment": {'automatic_placement': True},
            "properties": {'instance_type': partial_match('m1.large')}
        }

    if provider.one_of(GCEProvider):
        return {
            "catalog": {'vm_name': vm_name, 'catalog_name': {'name': template_name}},
            "environment": {'automatic_placement': True},
            "properties": {'instance_type': partial_match('1.8 GB'),
                           'boot_disk_size': partial_match('50'), 'is_preemptible': True}
        }


@pytest.fixture(scope='module')
def test_domain(appliance):
    domain = appliance.collections.domains.create('test_{}'.format(fauxfactory.gen_alphanumeric()),
                                                  'description_{}'.format(
                                                      fauxfactory.gen_alphanumeric()),
                                                  enabled=True)
    yield domain
    if domain.exists:
        domain.delete()


@pytest.fixture
def catalog_item(appliance, provider, dialog, catalog, prov_data):
    collection = appliance.collections.catalog_items
    catalog_item = collection.create(
        provider.catalog_item_type,
        name='test_{}'.format(fauxfactory.gen_alphanumeric()),
        description='test catalog',
        display_in=True,
        catalog=catalog,
        dialog=dialog,
        prov_data=prov_data)
    yield catalog_item
    if catalog_item.exists:
        catalog_item.delete()


@pytest.fixture(scope='module')
def max_quota_test_instance(appliance, test_domain):
    miq = appliance.collections.domains.instantiate('ManageIQ')

    original_instance = miq. \
        namespaces.instantiate('System'). \
        namespaces.instantiate('CommonMethods'). \
        classes.instantiate('QuotaMethods'). \
        instances.instantiate('quota_source')
    original_instance.copy_to(domain=test_domain)

    original_instance = miq. \
        namespaces.instantiate('System'). \
        namespaces.instantiate('CommonMethods'). \
        classes.instantiate('QuotaStateMachine'). \
        instances.instantiate('quota')
    original_instance.copy_to(domain=test_domain)

    instance = test_domain. \
        namespaces.instantiate('System'). \
        namespaces.instantiate('CommonMethods'). \
        classes.instantiate('QuotaStateMachine'). \
        instances.instantiate('quota')
    yield instance


def set_entity_quota_source(max_quota_test_instance, entity):
    with update(max_quota_test_instance):
        max_quota_test_instance.fields = {'quota_source_type': {'value': entity}}


@pytest.fixture(params=[('groups', 'group', 'EvmGroup-super_administrator'),
                        ('users', 'user', 'Administrator')], ids=['group', 'user'], scope='module')
def entities(appliance, request, max_quota_test_instance):
    collection, entity, description = request.param
    set_entity_quota_source(max_quota_test_instance, entity)
    yield getattr(appliance.collections, collection).instantiate(description)


@pytest.fixture(scope='function')
def set_entity_quota_tag(request, entities, appliance):
    tag, value = request.param
    tag = Tag(display_name=value, category=Category(display_name=tag))
    entities.add_tag(tag)
    yield
    # will refresh page as navigation to configuration is blocked if alert are on requests page
    appliance.server.browser.refresh()
    entities.remove_tag(tag)


# OpenStackProvider has infra issue, so skipping the test for it.
@pytest.mark.uncollectif(lambda provider, appliance: provider.one_of(OpenStackProvider))
@pytest.mark.parametrize(
    ['set_entity_quota_tag'],
    [
        [('Quota - Max Memory *', '1GB')],
        [('Quota - Max Storage *', '10GB')],
        [('Quota - Max CPUs *', '1')]
    ],
    indirect=['set_entity_quota_tag'],
    ids=['max_memory', 'max_storage', 'max_cpu']
)
def test_quota_tagging_cloud_via_lifecycle(request, appliance, provider, prov_data, setup_provider,
                                           set_entity_quota_tag, template_name, vm_name):
    """Test Group and User Quota in UI using tagging"""
    recursive_update(prov_data, {
        'request': {'email': 'test_{}@example.com'.format(fauxfactory.gen_alphanumeric())}})
    instance = Instance.factory(vm_name, provider, template_name)
    instance.create(**prov_data)
    # nav to requests page to check quota validation
    request_description = 'Provision from [{}] to [{}]'.format(template_name, vm_name)
    provision_request = appliance.collections.requests.instantiate(request_description)
    provision_request.wait_for_request(method='ui')
    request.addfinalizer(provision_request.remove_request)
    assert provision_request.row.reason.text == "Quota Exceeded"


# OpenStackProvider has infra issue, so skipping the test for it.
@pytest.mark.uncollectif(lambda provider, appliance: provider.one_of(OpenStackProvider))
@pytest.mark.parametrize('context', [ViaSSUI, ViaUI])
@pytest.mark.parametrize(
    ['set_entity_quota_tag'],
    [
        [('Quota - Max Memory *', '1GB')],
        [('Quota - Max Storage *', '10GB')],
        [('Quota - Max CPUs *', '1')]
    ],
    indirect=['set_entity_quota_tag'],
    ids=['max_memory', 'max_storage', 'max_cpu']
)
def test_quota_tagging_cloud_via_services(appliance, request, provider, setup_provider, context,
                                          admin_email, set_entity_quota_tag, catalog_item):
    """Test Group and User Quota in UI and SSUI using tagging"""
    with appliance.context.use(context):
        service_catalogs = ServiceCatalogs(appliance, catalog_item.catalog, catalog_item.name)
        if context is ViaSSUI:
            service_catalogs.add_to_shopping_cart()
        service_catalogs.order()
    # nav to requests page to check quota validation
    request_description = 'Provisioning Service [{0}] from [{0}]'.format(catalog_item.name)
    provision_request = appliance.collections.requests.instantiate(request_description)
    provision_request.wait_for_request(method='ui')
    request.addfinalizer(provision_request.remove_request)
    assert provision_request.row.reason.text == "Quota Exceeded"
