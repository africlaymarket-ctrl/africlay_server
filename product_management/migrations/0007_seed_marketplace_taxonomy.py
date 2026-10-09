from django.db import migrations


CATEGORY_TREE = (
    ('Home & Living', 'home-and-living', (
        ('Ceramics & Pottery', 'ceramics-and-pottery', (
            ('Tableware', 'tableware'),
            ('Decorative Pottery', 'decorative-pottery'),
        )),
        ('Basketry', 'basketry', (
            ('Storage Baskets', 'storage-baskets'),
            ('Market Baskets', 'market-baskets'),
        )),
        ('Home Textiles', 'home-textiles', (
            ('Cushions & Covers', 'cushions-and-covers'),
            ('Rugs & Throws', 'rugs-and-throws'),
        )),
        ('Woodcraft', 'woodcraft', (
            ('Kitchen & Dining Wood', 'kitchen-and-dining-wood'),
            ('Furniture & Decor', 'furniture-and-decor'),
        )),
    )),
    ('Fashion & Accessories', 'fashion-and-accessories', (
        ('Clothing', 'clothing', (
            ('Mens Clothing', 'mens-clothing'),
            ('Womens Clothing', 'womens-clothing'),
            ('Childrens Clothing', 'childrens-clothing'),
        )),
        ('Footwear', 'footwear', (
            ('Sandals', 'sandals'),
            ('Shoes', 'shoes'),
        )),
        ('Bags & Wallets', 'bags-and-wallets', (
            ('Handbags & Wallets', 'handbags-and-wallets'),
            ('Backpacks & Travel Bags', 'backpacks-and-travel-bags'),
        )),
        ('Jewelry & Accessories', 'jewelry-and-accessories', (
            ('Jewelry', 'jewelry'),
            ('Belts & Accessories', 'belts-and-accessories'),
        )),
    )),
    ('Beauty & Wellness', 'beauty-and-wellness', (
        ('Skincare', 'skincare', (
            ('Face Care', 'face-care'),
            ('Body Care', 'body-care'),
        )),
        ('Haircare', 'haircare', (
            ('Natural Hair Products', 'natural-hair-products'),
            ('Hair Accessories', 'hair-accessories'),
        )),
        ('Personal Care', 'personal-care', (
            ('Soaps & Bath', 'soaps-and-bath'),
            ('Fragrances', 'fragrances'),
        )),
        ('Wellness', 'wellness', (
            ('Herbal Wellness', 'herbal-wellness'),
            ('Fitness & Recovery', 'fitness-and-recovery'),
        )),
    )),
    ('Food & Pantry', 'food-and-pantry', (
        ('Grains & Flour', 'grains-and-flour', (
            ('Whole Grains', 'whole-grains'),
            ('Milled Flour', 'milled-flour'),
        )),
        ('Tea Coffee & Beverages', 'tea-coffee-and-beverages', (
            ('Tea & Infusions', 'tea-and-infusions'),
            ('Coffee & Cocoa', 'coffee-and-cocoa'),
        )),
        ('Spices & Condiments', 'spices-and-condiments', (
            ('Spices & Seasonings', 'spices-and-seasonings'),
            ('Sauces & Preserves', 'sauces-and-preserves'),
        )),
        ('Snacks & Baked Goods', 'snacks-and-baked-goods', (
            ('Snacks', 'snacks'),
            ('Baked Goods', 'baked-goods'),
        )),
        ('Fresh & Natural Foods', 'fresh-and-natural-foods', (
            ('Honey', 'honey'),
            ('Dried Fruits & Nuts', 'dried-fruits-and-nuts'),
        )),
    )),
    ('Agriculture & Farm', 'agriculture-and-farm', (
        ('Seeds & Seedlings', 'seeds-and-seedlings', (
            ('Vegetable Seeds', 'vegetable-seeds'),
            ('Cereal & Legume Seeds', 'cereal-and-legume-seeds'),
            ('Fruit & Tree Seedlings', 'fruit-and-tree-seedlings'),
        )),
        ('Farm Inputs', 'farm-inputs', (
            ('Fertilizers & Soil Care', 'fertilizers-and-soil-care'),
            ('Crop Protection', 'crop-protection'),
            ('Irrigation Supplies', 'irrigation-supplies'),
        )),
        ('Livestock & Poultry', 'livestock-and-poultry', (
            ('Animal Feed', 'animal-feed'),
            ('Poultry Supplies', 'poultry-supplies'),
            ('Dairy & Livestock Supplies', 'dairy-and-livestock-supplies'),
        )),
        ('Farm Tools & Equipment', 'farm-tools-and-equipment', (
            ('Hand Tools', 'hand-tools'),
            ('Machinery & Equipment', 'machinery-and-equipment'),
            ('Greenhouse Supplies', 'greenhouse-supplies'),
        )),
        ('Fresh Farm Produce', 'fresh-farm-produce', (
            ('Fruits & Vegetables', 'fruits-and-vegetables'),
            ('Grains & Pulses', 'grains-and-pulses'),
            ('Eggs Dairy & Honey', 'eggs-dairy-and-honey'),
        )),
    )),
    ('Electronics', 'electronics', (
        ('Mobile Phones & Accessories', 'mobile-phones-and-accessories', (
            ('Smartphones', 'smartphones'),
            ('Phone Accessories', 'phone-accessories'),
        )),
        ('Computers & Accessories', 'computers-and-accessories', (
            ('Laptops & Tablets', 'laptops-and-tablets'),
            ('Computer Accessories', 'computer-accessories'),
        )),
        ('Audio & Entertainment', 'audio-and-entertainment', (
            ('Speakers & Audio', 'speakers-and-audio'),
            ('TVs & Media', 'tvs-and-media'),
        )),
        ('Power & Appliances', 'power-and-appliances', (
            ('Solar & Power', 'solar-and-power'),
            ('Small Appliances', 'small-appliances'),
        )),
    )),
)


TAGS = (
    ('Handmade', 'handmade', ()),
    ('Locally Made', 'locally-made', ()),
    ('Made in Africa', 'made-in-africa', ()),
    ('Made to Order', 'made-to-order', ()),
    ('Customizable', 'customizable', ()),
    ('Natural Materials', 'natural-materials', ()),
    ('Recycled', 'recycled', ()),
    ('Upcycled', 'upcycled', ()),
    ('Wholesale', 'wholesale', ()),
    ('Bulk Available', 'bulk-available', ()),
    ('Gift Ready', 'gift-ready', ()),
    ('Limited Production', 'limited-production', ()),
    ('Handwoven', 'handwoven', ('home-and-living', 'fashion-and-accessories')),
    ('Hand Carved', 'hand-carved', ('home-and-living',)),
    ('Hand Painted', 'hand-painted', ('home-and-living',)),
    ('Food Safe', 'food-safe', ('home-and-living',)),
    ('Hand Stitched', 'hand-stitched', ('fashion-and-accessories',)),
    ('Unisex', 'unisex', ('fashion-and-accessories',)),
    ('Adjustable', 'adjustable', ('fashion-and-accessories',)),
    ('Natural Fiber', 'natural-fiber', ('fashion-and-accessories',)),
    ('Plant Based', 'plant-based', ('beauty-and-wellness',)),
    ('Fragrance Free', 'fragrance-free', ('beauty-and-wellness',)),
    ('Shea Butter', 'shea-butter', ('beauty-and-wellness',)),
    ('Essential Oils', 'essential-oils', ('beauty-and-wellness',)),
    ('Naturally Dried', 'naturally-dried', ('food-and-pantry', 'agriculture-and-farm')),
    ('Single Origin', 'single-origin', ('food-and-pantry',)),
    ('Caffeine Free', 'caffeine-free', ('food-and-pantry',)),
    ('Farm Fresh', 'farm-fresh', ('agriculture-and-farm', 'food-and-pantry')),
    ('Locally Grown', 'locally-grown', ('agriculture-and-farm', 'food-and-pantry')),
    ('Drought Resistant', 'drought-resistant', ('agriculture-and-farm',)),
    ('Early Maturing', 'early-maturing', ('agriculture-and-farm',)),
    ('Disease Resistant', 'disease-resistant', ('agriculture-and-farm',)),
    ('Certified Seed', 'certified-seed', ('agriculture-and-farm',)),
    ('Hybrid', 'hybrid', ('agriculture-and-farm',)),
    ('Open Pollinated', 'open-pollinated', ('agriculture-and-farm',)),
    ('Greenhouse Grown', 'greenhouse-grown', ('agriculture-and-farm',)),
    ('Rain Fed', 'rain-fed', ('agriculture-and-farm',)),
    ('Irrigated', 'irrigated', ('agriculture-and-farm',)),
    ('Seasonal', 'seasonal', ('agriculture-and-farm', 'food-and-pantry')),
    ('Smallholder Grown', 'smallholder-grown', ('agriculture-and-farm', 'food-and-pantry')),
    ('Refurbished', 'refurbished', ('electronics',)),
    ('Warranty Available', 'warranty-available', ('electronics',)),
    ('Energy Efficient', 'energy-efficient', ('electronics',)),
    ('Solar Powered', 'solar-powered', ('electronics', 'agriculture-and-farm')),
)


def seed_taxonomy(apps, schema_editor):
    Category = apps.get_model('product_management', 'Category')
    Tag = apps.get_model('product_management', 'Tag')
    categories_by_slug = {}

    def ensure_category(name, slug, parent, display_order):
        category = Category.objects.filter(slug=slug).first()
        if category is None:
            category = Category.objects.filter(name__iexact=name).first()
        created = category is None
        if created:
            category = Category.objects.create(
                name=name,
                slug=slug,
                parent=parent,
                display_order=display_order,
            )
        update_fields = []
        if not created and parent is not None and category.parent_id is None:
            category.parent = parent
            update_fields.append('parent')
        if update_fields:
            category.save(update_fields=update_fields)
        categories_by_slug[slug] = category
        return category

    for root_order, (root_name, root_slug, branches) in enumerate(CATEGORY_TREE, start=1):
        root = ensure_category(root_name, root_slug, None, root_order * 100)
        for branch_order, (branch_name, branch_slug, leaves) in enumerate(branches, start=1):
            branch = ensure_category(branch_name, branch_slug, root, branch_order * 10)
            for leaf_order, (leaf_name, leaf_slug) in enumerate(leaves, start=1):
                ensure_category(leaf_name, leaf_slug, branch, leaf_order)

    for name, slug, category_slugs in TAGS:
        tag = Tag.objects.filter(slug=slug).first()
        if tag is None:
            tag = Tag.objects.filter(name__iexact=name).first()
        if tag is None:
            tag = Tag.objects.create(name=name, slug=slug)
        categories = [categories_by_slug[item] for item in category_slugs if item in categories_by_slug]
        if categories:
            tag.categories.add(*categories)


class Migration(migrations.Migration):

    dependencies = [
        ('product_management', '0006_tag_categories'),
    ]

    operations = [
        migrations.RunPython(seed_taxonomy, migrations.RunPython.noop),
    ]
