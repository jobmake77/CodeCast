from setuptools import find_packages, setup


setup(
    name="codecast",
    version="0.1.0",
    description="CodeCast MVP: collect git pushes and publish drafts via opencli",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={
        "console_scripts": [
            "codecast=codecast.cli:main",
        ]
    },
    python_requires=">=3.9",
)

