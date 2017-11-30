# -*- coding: utf-8 -*-
import fauxfactory
import pytest

from cfme.utils.conf import cfme_data
from cfme.common.provider import cleanup_vm
from cfme.infrastructure.provider import InfraProvider
from cfme.infrastructure.pxe import get_template_from_config, ISODatastore
from cfme.provisioning import do_vm_provisioning
from cfme.utils import testgen

pytestmark = [
    pytest.mark.meta(server_roles="+automate"),
    pytest.mark.usefixtures('uses_infra_providers'),
    pytest.mark.tier(2)
]


def pytest_generate_tests(metafunc):
    # Filter out providers without provisioning data or hosts defined
    argnames, argvalues, idlist = testgen.providers_by_class(
        metafunc, [InfraProvider], required_fields=[
            ('iso_datastore', True),
            ['provisioning', 'host'],
            ['provisioning', 'datastore'],
            ['provisioning', 'iso_template'],
            ['provisioning', 'iso_file'],
            ['provisioning', 'iso_kickstart'],
            ['provisioning', 'iso_root_password'],
            ['provisioning', 'iso_image_type'],
            ['provisioning', 'vlan'],
        ])
    argnames = argnames + ['iso_cust_template', 'iso_datastore']

    new_idlist = []
    new_argvalues = []
    for i, argvalue_tuple in enumerate(argvalues):
        args = dict(zip(argnames, argvalue_tuple))
        if args['provider'].type == "scvmm":
            continue

        iso_cust_template = args['provider'].data['provisioning']['iso_kickstart']
        if iso_cust_template not in cfme_data.get('customization_templates', {}).keys():
            continue

        argvalues[i].append(get_template_from_config(iso_cust_template))
        argvalues[i].append(ISODatastore(args['provider'].name))
        new_idlist.append(idlist[i])
        new_argvalues.append(argvalues[i])

    testgen.parametrize(metafunc, argnames, new_argvalues, ids=new_idlist, scope="module")


@pytest.fixture
def datastore_init(appliance, iso_cust_template, iso_datastore, provisioning):
    if not iso_datastore.exists():
        collection = appliance.collections.iso_datastores
        collection.create(iso_datastore)
    # Fails on upstream, BZ1109256
    iso_datastore.set_iso_image_type(provisioning['iso_file'], provisioning['iso_image_type'])
    if not iso_cust_template.exists():
        iso_cust_template.create()


@pytest.fixture(scope="function")
def vm_name():
    vm_name = 'test_iso_prov_{}'.format(fauxfactory.gen_alphanumeric(8))
    return vm_name


@pytest.mark.tier(2)
def test_iso_provision_from_template(appliance, provider, vm_name, smtp_test, datastore_init,
                                     request, setup_provider):
    """Tests ISO provisioning

    Metadata:
        test_flag: iso, provision
        suite: infra_provisioning
    """
    # generate_tests makes sure these have values
    iso_template, host, datastore, iso_file, iso_kickstart,\
        iso_root_password, iso_image_type, vlan = map(provider.data['provisioning'].get,
            ('pxe_template', 'host', 'datastore', 'iso_file', 'iso_kickstart',
             'iso_root_password', 'iso_image_type', 'vlan'))

    request.addfinalizer(lambda: cleanup_vm(vm_name, provider))

    provisioning_data = {
        'catalog': {
            'vm_name': vm_name,
            'provision_type': 'ISO',
            'iso_file': {'name': iso_file}},
        'environment': {
            'host_name': {'name': host},
            'datastore_name': {'name': datastore}},
        'customize': {
            'custom_template': {'name': iso_kickstart},
            'root_password': iso_root_password},
        'network': {
            'vlan': vlan}}

    do_vm_provisioning(appliance, iso_template, provider, vm_name, provisioning_data, request,
                       smtp_test, num_sec=1500)
