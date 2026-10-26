from setuptools import setup, find_packages

PACKAGE_NAME = "glue_etl"

setup_kwargs = dict(name=PACKAGE_NAME,
                    version="0.1",
                    descripton="Contains main module executed by AWS Glue job",
                    url="",
                    author="Alexandre Pinto",
                    packages=find_packages(include=[PACKAGE_NAME, "().*".format(PACKAGE_NAME)]),
                    package_dir={"": "."}
                    )
setup(**setup_kwargs)