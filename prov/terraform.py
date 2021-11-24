import json
import os
import subprocess
import inspect
import shutil

import yaml

def apply(deployment_name, terraform_plan, config):
    config['private_key_location'] = os.path.realpath(config['private_key_location'])
    config['public_key_location'] = os.path.realpath(config['public_key_location'])

    if not os.path.isdir(terraform_plan):
        print(f"Could not find directory [{terraform_plan}]")
        exit(1)

    tf_vars = {f"TF_VAR_{k}":f"{v}" for (k, v) in config.items()}

    call_env = {
        "shell": True,
        "env": {
            "TF_WORKSPACE": "default",
            #"TF_LOG": "trace",
            **tf_vars,
        },
        "cwd": terraform_plan,
    }

    cmd = f'terraform workspace new {deployment_name}'
    exitcode = subprocess.call(cmd, **call_env)
    call_env["env"]["TF_WORKSPACE"] = deployment_name;

    cmd = f'terraform init'
    exitcode = subprocess.call(cmd, **call_env)
    if exitcode != 0:
        raise Exception(f'Failed terraform init, plan [{terraform_plan}], exitcode={exitcode} command=[{cmd}])')

    if not os.path.isdir(f"{deployment_name}"):
        os.makedirs(f"{deployment_name}")

    with open(f'{deployment_name}/tfvars.candidate.json', 'w') as f:
        json.dump(config, f);

    cmd = f'terraform apply'
    exitcode = subprocess.call(cmd, **call_env)    
    if exitcode != 0:
        raise Exception(f'Failed terraform apply, plan [{terraform_plan}], exitcode={exitcode} command=[{cmd}])')
    
    output_text = subprocess.check_output(f'terraform output -json', **call_env, text=True)
    output = json.loads(output_text)

    environment = {}
    for key, value in output.items():
        environment[key] = output[key]['value']

    with open(f'{deployment_name}/ssh_config', 'w') as ssh_config_file:
        ssh_config_content = f"""Host *
    ServerAliveInterval 120
    StrictHostKeyChecking no
    IdentityFile {config['private_key_location']}
    ForwardAgent yes
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlMaster auto
    ControlPersist 10m
    ConnectionAttempts 60
Host server-*
    User {config["server_user"]}
Host client-*
    User {config["client_user"]}
Host monitoring-*
    User {config["monitoring_user"]}
"""
        for node_type in ["server", "client", "monitoring"]:
            for i, ip in enumerate(environment[f"{node_type}_public_ips"]):
                ssh_config_content += f"""
Host {node_type}-{i}
    HostName {ip}"""
        print(ssh_config_content, file=ssh_config_file)

    with open(f'{deployment_name}/tfvars.json', 'w') as f:
        json.dump(config, f);

    with open(f'{deployment_name}/inventory', 'w') as f:
        content = f"""[server:vars]
seed={environment["server_private_ips"][0]}
"""
        for node_type in ["server", "client", "monitoring"]:
            content += f"""[{node_type}]
"""
            for i, (public_ip, private_ip) in enumerate(zip(environment[f"{node_type}_public_ips"], environment[f"{node_type}_private_ips"])):
                content += f"""{node_type}-{i} public_ip={public_ip} private_ip={private_ip}
"""
        print(content, file=f)

    try:
        os.symlink(terraform_plan, f'{deployment_name}/terraform_plan')
    except FileExistsError:
        pass

def destroy(deployment_name):
    terraform_plan = os.readlink(f'{deployment_name}/terraform_plan')

    if not os.path.isdir(terraform_plan):
        print(f"Could not find directory [{terraform_plan}]")
        exit(1)

    call_env = {
        "shell": True,
        "env": {
            "TF_WORKSPACE": deployment_name,
            #"TF_LOG": "trace",
        },
        "cwd": terraform_plan,
    }
   
    varfile = os.path.realpath(f"{deployment_name}/tfvars.json")
    cmd = f'terraform destroy -var-file {varfile}'
    exitcode = subprocess.call(cmd, **call_env)
    if exitcode != 0:
        raise Exception(f'Failed terraform destroy, plan [{terraform_plan}], exitcode={exitcode} command=[{cmd}])')

    shutil.rmtree(f"{deployment_name}")

    call_env["env"]["TF_WORKSPACE"] = "default";
    cmd = f'terraform workspace delete {deployment_name}'
    exitcode = subprocess.call(cmd, **call_env)
    if exitcode != 0:
        raise Exception(f'Failed terraform workspace, plan [{terraform_plan}], exitcode={exitcode} command=[{cmd}])')
