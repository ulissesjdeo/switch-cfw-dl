pip install nuitka

python \
  -m nuitka \
  --onefile \
  --standalone \
  --python-flag=-OO \
  --python-flag=no_site \
  --lto=yes \
  switch_cfw_dl.py
