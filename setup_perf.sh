#sudo apt-get update

sudo apt-get install -y git binutils linux-tools-common
sudo apt-get install -y linux-tools-`uname -r`

git config --global user.email "you@example.com"
git config --global user.name "Your Name"
git clone https://github.com/brendangregg/FlameGraph
cd FlameGraph/
git remote add fix https://github.com/adamnovak/FlameGraph
git fetch fix
git merge fix/handle-template-parens -m "fix"
cd ..
