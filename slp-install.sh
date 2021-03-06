#!/bin/bash

VENVDIR="$HOME/.local/share/slp/venv"
GITREPO="https://github.com/Solar-network/python-slp.git"

clear

if [ $# = 0 ]; then
    B="sxp-devnet"
else
    B=$1
fi
echo "branch selected = $B"

echo
echo installing system dependencies
echo ==============================
sudo apt-get -qq install python3 python3-dev python3-setuptools python3-pip
sudo apt-get -qq install virtualenv
echo "done"

echo
echo downloading python-slp package
echo ==============================

cd ~
if (git clone --branch $B $GITREPO) then
    echo "package cloned !"
    cd ~/python-slp
else
    echo "package already cloned !"
    cd ~/python-slp
    git reset --hard
    git fetch --all
    git checkout $B -f
    git pull
fi

echo "done"

echo
echo creating virtual environment
echo =============================

if [ -d $VENVDIR ]; then
    read -p "remove previous virtual environement ? [y/N]> " r
    case $r in
    y) rm -rf $VENVDIR;;
    Y) rm -rf $VENVDIR;;
    *) echo -e "previous virtual environement keeped";;
    esac
fi

TARGET="$(which python3)"
virtualenv -p $TARGET $VENVDIR -q

echo "done"

# install python dependencies
echo
echo installing python dependencies
echo ==============================
. $VENVDIR/bin/activate
export PYTHONPATH=$HOME/python-slp
cd ~/python-slp
pip install -r requirements.txt -q
echo "done"

echo
echo deploying node and api
echo ======================
python -c "import app;app.deploy(host='0.0.0.0', port=5200, blockchain='sxp')"

echo
echo "setup finished"
