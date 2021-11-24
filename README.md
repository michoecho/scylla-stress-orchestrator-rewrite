WIP

I have just made large untested changes, so the repo may not work correctly at the moment. Don't try to use it yet.

Main differences w.r.t. scylla-stress-orchestrator:
- Benchmarking scripts are written in async python, instead of custom futures. I think this is much cleaner.
- The provisioning script generates an Ansible inventory and a ssh config file from Terraform's output. All further access to VMs is done by hostnames and those config files. This simplifies scripts and helps with interactive work.
- Prometheus metrics are downloaded using the snapshot API, instead of stopping monitoring, downloading data and restarting monitoring. Restarting monitoring is slow.
- Configuration scripts are written in ansible, rather than in ssh calls from python and terraform. (Not a very important difference. I'm not sure which approach is the better one yet.)

Example usage:

```
# Provision the servers, clients and monitoring and generate configs to test_deployment/
bin/provision-terraform ec2-spot test_deployment c5d.4xlarge.yml credentials.yml us-east-2.yml

# Install software and set configs on the provisioned VMs.
parallel --lb bin/ansible-playbook test_deployment configure_{}.playbook ::: servers clients monitoring

# Start Scylla and monitoring.
parallel --lb bin/ansible-playbook test_deployment reset_{}.playbook ::: servers monitoring

# Run the benchmark and put output in trials/test_deployment_$DATETIME/
python benchmark.py test_deployment

# Do an interactive ssh session on server-0.
bin/ssh test_deployment server-0

# Open the live dashboards in firefox.
bin/firefox-monitoring test_deployment
```
