# Wordpress Updater

wordpress-updater is a script I coded for my digitalocean LAMP droplets that
run wordpress sites. I prefer to update all the time, and deal with websites
breaking than deal with websites being abused and the associated problems, see below for an example run:

![Wordpress Updater example run](/screenshots/wpupdater.png?raw=true "wpupdater doing its thing")

Basically, it is a simple script to call wp-cli {core,plugin,theme} update
on each wordpress setup it finds. It uses Apache's configuration files to
get DocumentRoot entries to initiate searches for wordpress instances.

It then runs wp-cli on those locations, according to command line options.

It supports core update, plugin update --all, theme update --all,
transient delete --expired.

Additionally, it supports conditional execution when run with --tags
parameters. This functionality is only available on DigitalOcean droplets.

Installation is done through pip3, please check the wiki for more
information: https://www.github.com/buanzo/wordpress-updater/wiki

Since version 0.5, wordpress-updaters supports reporting of non-zero exit
status of wp-cli commands, and other errors, using Hume:
https://www.github.com/buanzo/hume/wiki

Cheers!

Arturo 'Buanzo' Busleiman
