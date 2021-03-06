# Flux-CI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) 
[![Join the chat at https://gitter.im/flux-ci/Lobby](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/flux-ci/Lobby)

Flux is a simple and lightweight continuous integration server that responds
to Webhooks that can be triggered by various Git hosting services.

Flux should be deployed over an SSL encrypted proxy pass server and be used
for internal purposes only since it does not provide mechanisms to prevent
bad code execution.

[View the full documentation ▸](https://niklasrosenstein.github.io/flux-ci)  

## Development
We recommend using a Python virtualenv to install Flux CI and its dependencies into.
Rerun the last command to restart Flux CI with latest local changes.


<table><tr><th>virtualenv</th><th>Pipenv</th><th>Execute main directly</th></tr>
<tr><td>

```
virtualenv .venv
source .venv/bin/activate
pip install -e .
flux-ci --web
```

</td><td>

```
PIPENV_VENV_IN_PROJECT=1 pipenv install -e .
pipenv run flux-ci --web
```

</td><td>

```
python -m flux.main
```

</td></tr>
</table>

## Screenshots

<table>
  <tr>
    <td><p align="center">Login</p><a href="https://i.imgur.com/NIlRIFI.png"><img src="https://i.imgur.com/NIlRIFI.png"/></a></td>
    <td><p align="center">Dashboard</p><a href="https://i.imgur.com/m5R4syk.png"><img src="https://i.imgur.com/m5R4syk.png"/></a></td>
    <td><p align="center">Repositories</p><a href="https://i.imgur.com/5CAR1zs.png"><img src="https://i.imgur.com/5CAR1zs.png"/></a></td>
    <td><p align="center">Repository Overview</p><a href="https://i.imgur.com/Fm6GyPI.png"><img src="https://i.imgur.com/Fm6GyPI.png"/></a></td>
    <td><p align="center">Build overview</p><a href="https://i.imgur.com/CGEYRB6.png"><img src="https://i.imgur.com/CGEYRB6.png"/></a></td>
  </tr>
  <tr>
    <td><p align="center">Users</p><a href="https://i.imgur.com/hZ7FQix.png"><img src="https://i.imgur.com/hZ7FQix.png"/></a></td>
    <td><p align="center">User Settings</p><a href="https://i.imgur.com/TMNn9ol.jpg"><img src="https://i.imgur.com/TMNn9ol.jpg"/></a></td>
    <td><p align="center">Integration</p><a href="https://i.imgur.com/FumaPOQ.png"><img src="https://i.imgur.com/FumaPOQ.png"/></a></td>
    <td><p align="center">Confirmation</p><a href="https://i.imgur.com/60ox3PR.png"><img src="https://i.imgur.com/60ox3PR.png"/></a></td>
    <td><p align="center">Logo</p><a href="https://i.imgur.com/k18t1XA.png"><img src="https://i.imgur.com/k18t1XA.png"/></a></td>
  </tr>
</table>

---

<p align="center">Copyright &copy; 2018 Niklas Rosenstein</p>
