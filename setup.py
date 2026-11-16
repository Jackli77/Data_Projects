from setuptools import find_packages, setup

setup(
    name="kbo",
    version="0.1.0",
    description=(
        "Belgian KBO Open Data ETL pipeline: download archives and load them "
        "into PostgreSQL."
    ),
    packages=find_packages(exclude=("tests", "tests.*")),
    python_requires=">=3.10",
    install_requires=[
        "requests>=2.32,<3",
        "psycopg[binary]>=3.2,<4",
        "python-dotenv>=1.0,<2",
    ],
    extras_require={
        "test": ["pytest>=8"],
        # only needed by kbo.helper.inspect_data
        "debug": ["pandas>=2"],
    },
    entry_points={
        "console_scripts": [
            "kbo=kbo.main:main",
        ],
    },
)
