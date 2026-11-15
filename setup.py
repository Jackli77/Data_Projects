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
        "pandas",
        "requests",
        "pyarrow",
        "psycopg[binary]",
        "python-dotenv",
    ],
    extras_require={
        "test": ["pytest"],
    },
    entry_points={
        "console_scripts": [
            "kbo=kbo.main:main",
        ],
    },
)
