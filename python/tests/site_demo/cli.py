from importlib.resources import files


def main():
    print(files("site_demo").joinpath("default.json").read_text().strip())
