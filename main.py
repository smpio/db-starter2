import sys
import time
import atexit
import datetime
import contextlib

import kubernetes


namespace = 'smmplanner-prd'
pvc_name = 'data-postgres-clone-0'
snapshot_selector = 'snapshooter.smp.io/source=postgres-data'
storage_class = 'yc-network-temp-ssd'
stateful_set_name = 'postgres-clone'
service_name = stateful_set_name


def main():
    kubernetes.config.load_kube_config()
    core_api = kubernetes.client.CoreV1Api()
    custom_api = kubernetes.client.CustomObjectsApi()
    apps_api = kubernetes.client.AppsV1Api()

    print(f'Namespace: {namespace}')
    atexit.register(cleanup)

    if is_pod_ready():
        print('Database replica is already running')
        usage()
        return

    snapshots = custom_api.list_namespaced_custom_object(
        'snapshot.storage.k8s.io',
        'v1beta1',
        namespace,
        'volumesnapshots',
        label_selector=snapshot_selector,
    )

    if not snapshots['items']:
        print(f'No snapshots matching selector {snapshot_selector} in namespace {namespace}')
        return sys.exit(1)

    snapshot = sorted(snapshots['items'], key=lambda s: s['metadata']['creationTimestamp'])[-1]
    snapshot_name = snapshot['metadata']['name']
    original_pvc_name = snapshot['spec']['source']['persistentVolumeClaimName']
    original_pvc = core_api.read_namespaced_persistent_volume_claim(original_pvc_name, namespace)

    print(f'Found latest snapshot: {snapshot_name}')
    print(f'Found original PVC: {original_pvc_name}')

    pvc = core_api.create_namespaced_persistent_volume_claim(namespace, {
        'metadata': {
            'name': pvc_name,
            'annotations': {
                'snapshooter.smp.io/schedule': 'never',
            },
        },
        'spec': {
            'dataSource': {
                'name': snapshot_name,
                'kind': 'VolumeSnapshot',
                'apiGroup': 'snapshot.storage.k8s.io',
            },
            'accessModes': ['ReadWriteOnce'],
            'resources': {
                'requests': {
                    'storage': original_pvc.spec.resources.requests['storage'],
                },
            },
            'storageClassName': storage_class,
            'volumeMode': original_pvc.spec.volume_mode,
        },
    })

    print(f'Created PVC {pvc_name}')

    apps_api.patch_namespaced_stateful_set_scale(stateful_set_name, namespace, {
        'spec': {
            'replicas': 1,
        },
    })
    print(f'Scaled StatefulSet {stateful_set_name} to 1 replica')
    print('Current local time:', datetime.datetime.now())

    ready = False
    print('Waiting for pod to start-up (about 15 minutes)..', end='')
    while not ready:
        time.sleep(30)
        print('.', end='')
        ready = is_pod_ready()

    print('')
    print('Give a minute to initialize and you will be able to connect to the database')
    usage()


def cleanup():
    core_api = kubernetes.client.CoreV1Api()
    apps_api = kubernetes.client.AppsV1Api()

    apps_api.patch_namespaced_stateful_set_scale(stateful_set_name, namespace, {
        'spec': {
            'replicas': 0,
        },
    })
    print(f'Scaled StatefulSet {stateful_set_name} to 0 replicas')

    with IgnoreNotExist():
        core_api.delete_namespaced_persistent_volume_claim(pvc_name, namespace)
        print(f'Removed PVC {pvc_name}')


def is_pod_ready():
    apps_api = kubernetes.client.AppsV1Api()
    stateful_set = apps_api.read_namespaced_stateful_set(stateful_set_name, namespace)
    return stateful_set.status.ready_replicas == 1


def usage():
    print('Use the following command to access the database. Postgres port 5432 will be forwarded to your localhost.')
    print(f'kubectl -n {namespace} port-forward service/{service_name} 5432:5432')
    input('Press ENTER when finished. Database will be terminated')


class IgnoreNotExist(contextlib.AbstractContextManager):
    def __enter__(self):
        pass

    def __exit__(self, exctype, excinst, exctb):
        return exctype is not None and issubclass(exctype, kubernetes.client.exceptions.ApiException) \
               and excinst.status == 404


if __name__ == '__main__':
    main()

# role
# networkpolicy
