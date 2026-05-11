"""Page routes — just the index template."""

from flask import Blueprint, render_template


pages = Blueprint("pages", __name__)


@pages.get("/")
def index():
    return render_template("index.html")
