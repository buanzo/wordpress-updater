# -*- coding: utf-8 -*-
from setuptools import setup
from os import path

# Imports content of requirements.txt into setuptools' install_requires
with open('requirements.txt') as f:
    requirements = f.read().splitlines()


def get_version():
    with open('wordpressupdater.py') as f:
        for line in f:
            if line.startswith('__version__'):
                return eval(line.split('=')[-1])

setup(name='wordpressupdater',
      version=get_version(),
      description="Discovers and updates wordpress instances by reading Apache2 virtualhost configuration",
      long_description="Python module and CLI tool tool that reads Apache2 configuration files to let you automagically perform wp-cli updates and maintenance on discovered wordpress installations.",
      keywords="wordpress,wp-cli,wrapper,apache2,automagically,digitalocean,devops,sysadmin",
      author='Arturo "Buanzo" Busleiman',
      author_email='buanzo@buanzo.com.ar',
      url='https://github.com/buanzo/wordpress-updater',
      license='GPLv3',
      zip_safe=False,
      python_requires='>=3.6',
      py_modules=['wordpressupdater'],
      namespace_packages=[],
      include_package_data=True,
      install_requires=requirements,
      entry_points={
         'console_scripts': [
            'wpupdater = wordpressupdater:run',
         ],
      },
      classifiers=[
         'Environment :: Console',
         'Intended Audience :: Developers',
         'Intended Audience :: System Administrators',
         'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
         'Natural Language :: English',
         'Operating System :: POSIX :: Linux',
         'Operating System :: POSIX :: Other',
         'Operating System :: POSIX',
         'Programming Language :: Python',
         'Programming Language :: Python :: 3 :: Only',
         'Programming Language :: Python :: 3.6',
         'Programming Language :: Python :: 3.7',
         'Programming Language :: Python :: 3.8',
         'Programming Language :: Python :: Implementation :: PyPy',
      ])
