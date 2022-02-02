import subprocess

for i in range(5000, 5010):
    subprocess.Popen("python app.py -p %s" % i, shell=True)
