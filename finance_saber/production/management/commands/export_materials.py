from .export_recipes import Command as ExportRecipes


class Command(ExportRecipes):
    help = (
        "Write raw materials and packaging, with their current stock, to an .xlsx file, to fill an empty "
        "database on a new server with load_materials or the Raw materials page's upload. With --seed, write "
        "the project's materials file (production/seed/materials.xlsx) that is deployed with the code."
    )
    kind = "materials"
