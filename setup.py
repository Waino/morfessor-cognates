#!/usr/bin/env python

from codecs import open
from ez_setup import use_setuptools
use_setuptools()

from setuptools import setup

import re
main_py = open('morfessorcognate/__init__.py', encoding='utf-8').read()
metadata = dict(re.findall("__([a-z]+)__ = '([^']+)'", main_py))

requires = [
        'python-Levenshtein',
]

setup(name='Morfessor-cognate',
      version=metadata['version'],
      author=metadata['author'],
      author_email='morpho@aalto.fi',
      url='http://morpho.aalto.fi',
      description='Morfessor',
      packages=['morfessorcognate', 'morfessorcognate.constructions'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: BSD License',
          'Operating System :: OS Independent',
          'Programming Language :: Python',
          'Topic :: Scientific/Engineering',
      ],
      license="BSD",
      scripts=[#'scripts/morfessor',
               #'scripts/morfessor-train',
               #'scripts/morfessor-segment',
               #'scripts/morfessor-evaluate',
               #'scripts/tune_tokensync.py',
               #'scripts/tune_tokensync_init.py',
               #'scripts/tune_tokensync_pseudocounts.py',
               'scripts/morfessorcognate-train',
               'scripts/morfessorcognate-segment',
               ],
      install_requires=requires,
      extras_require={
          'docs': [l.strip() for l in open('docs/build_requirements.txt')]
      }
      )
