import docker 
from enums import AppType
from flask import Flask 
from flask import request, abort
import os
from werkzeug.wrappers.response import Response
import tarfile
import subprocess 

app = Flask(__name__)

WORKDIR = './temp'

@app.route("/package", methods=['POST'])
def package():
	container_name, repo_path = get_request_attrs(request.json)

	container = get_container(container_name)
	
	name = get_name(repo_path)
	tar_path = f'{WORKDIR}/{name}.tar'
	host_repo_path = copy_folder_to_host(container, repo_path, tar_path)

	package_path = package_application(host_repo_path)
	package_path = f'{repo_path}{package_path}' if repo_path[-1:] == '/' else f'{repo_path}/{package_path}'

	copy_folder_to_container(container, host_repo_path, repo_path, name)

	return f"Path: {package_path}"

@app.route("/test")
def test():
	return "test"

def get_request_attrs(json):
	try:
		return [json['containerName'], json['repoPath']]
	except KeyError:
		abort(Response("Expected 'containerName' and 'repoPath' to be present.", status=400))

def get_container(container_name):
	client = docker.from_env()

	try:
		return client.containers.get(container_name)
	except docker.errors.NotFound:
		abort(Response(f"'{container_name}' is not a valid container name.", status=400))
	except docker.errors.APIError:
		abort(Response(f"An unexpected error occurred while getting the container.", status=500))

def get_name(path):
	basename = os.path.basename(path)
	name, __ = os.path.splitext(basename)
	return name 

def copy_folder_to_host(container, container_path, host_path):
	folder_path = f'{WORKDIR}/{get_name(host_path)}'

	try:
		bits, stat = container.get_archive(container_path)

		if not os.path.exists(WORKDIR):
			os.mkdir(WORKDIR)

		with open(host_path, 'wb') as f:
			for chunk in bits:
				f.write(chunk)

		with tarfile.open(host_path) as tar:
			tar.extractall(path=WORKDIR)
		
		return folder_path
	except docker.errors.APIError:
		abort(Response("An unexpected error occurred while copying the docker folder/file to the host. Please ensure the provided path exists.", status=500))
	except PermissionError:
		return folder_path

def package_application(path):
	files = os.listdir(path)
	app_type = get_app_type(files)
	if app_type == None:
		abort(Response("Could not determine the type of project. Please ensure the repository is a valid programming project."))

	completed_process = None
	if 'package.sh' in files:
		completed_process = subprocess.run(['./package.sh'])
	elif app_type == AppType.CSHARP:
		completed_process = subprocess.run(['dotnet', 'publish'], shell=True, cwd=path)
	elif app_type == AppType.JAVA:
		completed_process = subprocess.run(['mvn', 'package', '-DskipTests'], shell=True, cwd=path)
	elif app_type == AppType.ELECTRON:
		completed_process = subprocess.run(['npm', 'install'], shell=True, cwd=path)
		if completed_process.returncode != 0:
			abort(Response("Could not install dependencies.")) 

		completed_process = subprocess.run(['npm', 'run', 'package'], shell=True, cwd=path)
	else:
		abort(Response("Could not package application. Please ensure the project is one of the supporting types: \
		\n\t- C-SHARP\n\t-JAVA\n\t-ELECTRON"))

	if completed_process and completed_process.returncode == 0:
		return get_package_path(app_type, path)
	else:
		abort(Response("An unknown error occurred while packaging.", status=500))

def get_app_type(files):
	scores = {
		AppType.CSHARP: 0,
		AppType.JAVA: 0,
		AppType.ELECTRON: 0,
	}

	for file in files:
		if '.cs' in file or '.csproj' in file:
			scores[AppType.CSHARP] += 1
		if '.java' in file or 'pom.xml' in file:
			scores[AppType.JAVA] += 1
		if 'package.json' in file:
			scores[AppType.ELECTRON] += 1

	likeliest_type = None
	max_score = 0
	for app_type in AppType.__members__.values():
		score = scores[app_type]

		if score > max_score:
			max_score = score 
			likeliest_type = app_type

	return likeliest_type

def get_package_path(app_type, path):
	if app_type == AppType.CSHARP:
		return 'bin/Debug/net6.0/publish'
	elif app_type == AppType.JAVA:
		return get_java_package_path(path)
	elif app_type == AppType.ELECTRON:
		folder = os.listdir(f'{path}/out')[0]
		return f'out/{folder}'

def get_java_package_path(path):
	if 'shade' in os.listdir(path):
		file = [file for file in os.listdir(f'{path}/shade') if '.jar' in file][0]
		return f'shade/{file}'
	else:
		file = [file for file in os.listdir(f'{path}/target') if '.jar' in file][0]
		return f'target/{file}'


def copy_folder_to_container(container, host_path, container_path, name):
	tar_file = f'{WORKDIR}/{name}.tar'

	with tarfile.open(name=tar_file, mode='w') as tar:
		tar.add(host_path, arcname=name)

	container_path = os.path.dirname(container_path)
	with open(tar_file, 'rb') as f:
		data = f.read()
		container.put_archive(container_path, data)