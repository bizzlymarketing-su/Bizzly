from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
import sqlite3
from database import db, Entrepreneur, Notification, Coupon, Subscription
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
import os
import uuid
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url

DB_available = True
app = Flask(__name__)

# verbind met SQLite database
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get('SECRET_KEY')
db.init_app(app)
migrate = Migrate(app, db)

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)


# database models

class Business(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('entrepreneur.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    address = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    image = db.Column(db.String(200))
    clicks = db.Column(db.Integer, default=0)
    category = db.Column(db.String(50), nullable=False)
    facebook_url = db.Column(db.String(300), nullable=True)

    products = db.relationship('Product', backref='business', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    image = db.Column(db.String(200), default="images/default_product.png")
    clicks = db.Column(db.Integer, default=0)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

    reviews = db.relationship('Review', backref='customer', lazy=True)
    wishlist_items = db.relationship('Wishlist', backref='customer', lazy=True)


class Wishlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))

    product = db.relationship('Product')

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)  # 1–5
    comment = db.Column(db.Text)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    business_id = db.Column(db.Integer, db.ForeignKey('business.id'))


def require_entrepreneur():
    if session.get("role") != "entrepreneur":
        flash("Alleen ondernemers hebben toegang.", "error")
        return False
    return True

def require_customer():
    if session.get("role") != "customer":
        flash("Alleen klanten hebben toegang.", "error")
        return False
    return True

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

UPLOAD_FOLDER = os.path.join(app.static_folder, "images")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )

def check_subscription(entrepreneur_id):
    sub = Subscription.query.filter_by(entrepreneur_id=entrepreneur_id).first()
    if not sub:
        return None   # geen subscription = niet geactiveerd

    now = datetime.utcnow()
    days_active = (now - sub.activated_at).days

    if sub.status == 'deactivated':
        return sub   # al gedeactiveerd, niks meer te doen

    # Dag 44+: deactiveer account
    if days_active >= 44:
        sub.status = 'deactivated'
        db.session.commit()

    # Dag 30-43: verlopen, sancties actief
    elif days_active >= 30 and sub.status != 'expired':
        sub.status = 'expired'
        # Stuur waarschuwingsnotificatie
        notif = Notification(
            entrepreneur_id=entrepreneur_id,
            title="⚠️ Abonnement verlopen!",
            message="Je abonnement is verlopen. Je bedrijf is niet meer zichtbaar. Voer een nieuwe coupon in om te verlengen.",
            type="subscription"
        )
        db.session.add(notif)
        db.session.commit()

    # Dag 25-29: bijna verlopen, reminder
    elif days_active >= 25 and sub.status == 'active':
        sub.status = 'expiring_soon'
        notif = Notification(
            entrepreneur_id=entrepreneur_id,
            title="🔔 Abonnement verloopt bijna",
            message=f"Je abonnement verloopt over {30 - days_active} dagen. Koop een nieuwe coupon om te verlengen.",
            type="subscription"
        )
        db.session.add(notif)
        db.session.commit()

    return sub


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        business_name = request.form['business_name']
        category = request.form['category']
        email = request.form['email']
        password = request.form['password']

        # check of email al bestaat
        existing_user = Entrepreneur.query.filter_by(email=email).first()
        if existing_user:
            flash("Dit e-mailadres is al geregistreerd.", "error")
            return redirect(url_for('signup'))

        # nieuwe ondernemer aanmaken
        new_user = Entrepreneur(
            name=name,
            business_name=business_name,
            category=category,
            email=email,
            password=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()

        # nieuw bedrijf aanmaken op basis van dezelfde info
        new_business = Business(
            owner_id=new_user.id,
            name=business_name,
            description=f"{business_name} is actief in de categorie {category}.",
            category=category,
            latitude=None,   # later instelbaar via locatieknop
            longitude=None,
            image="images/default_business.png"  # standaard afbeelding
        )
        db.session.add(new_business)
        db.session.commit()

        # ondernemer automatisch inloggen
        session.clear()
        session['role'] = 'entrepreneur'
        session['entrepreneur_id'] = new_user.id
        session['username'] = new_user.business_name

        flash("Account aangemaakt! Laten we nu je bedrijf instellen.", "success")
        return redirect(url_for('setup_business'))


    return render_template('signup.html')

@app.route('/setup_business', methods=['GET', 'POST'])
def setup_business():
    if session.get('role') != 'entrepreneur':
        return redirect(url_for('login'))

    business = Business.query.filter_by(
        owner_id=session['entrepreneur_id']
    ).first()

    if request.method == 'POST':
        business.description = request.form['description']
        business.address = request.form.get('address', '')
        user = Entrepreneur.query.get(session['entrepreneur_id'])
        user.accent_color = request.form.get('accent_color', '#7fff00')

        # logo upload (simpel)
        file = request.files.get('logo')

        file = request.files.get('logo')
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Ongeldig bestandstype", "error")
                return redirect(url_for('setup_business'))
            result = cloudinary.uploader.upload(file, folder="bizzly/businesses")
            business.image = result['secure_url']  # alleen dit wordt opgeslagen in de database
            flash("✅ Bedrijfslogo succesvol geüpload!", "success")
        db.session.commit()
        if business.description:
            return redirect(url_for('dashboard'))
        else:
            return redirect(url_for('add_products', business_id=business.id))

    return render_template('setup_business.html', business=business)

@app.route('/add_products/<int:business_id>', methods=['GET', 'POST'])
def add_products(business_id):

    if session.get('role') != 'entrepreneur':
        return redirect(url_for('login'))

    business = Business.query.get_or_404(business_id)

    if business.owner_id != session.get('entrepreneur_id'):
        flash("Geen toegang tot dit bedrijf.", "error")

    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        file = request.files.get('image')
        file = request.files.get('image')
        if file and file.filename and allowed_file(file.filename):
            result = cloudinary.uploader.upload(file, folder="bizzly/products")
            image = result['secure_url']
            flash("✅ Productfoto succesvol geüpload!", "success")
        else:
            image = "images/default_product.png"

        new_product = Product(
            business_id=business.id,
            name=name,
            description=description,
            image=image
        )

        db.session.add(new_product)
        db.session.commit()
        flash("Product toegevoegd!", "success")

        return redirect(url_for('add_products', business_id=business.id))

    products = Product.query.filter_by(business_id=business.id).all()
    return render_template('add_products.html', business=business, products=products)

@app.route('/edit_product/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    if session.get('role') != 'entrepreneur':

    product = Product.query.get_or_404(product_id)
    business = Business.query.get(product.business_id)

    if request.method == 'POST':
        product.name = request.form['name']
        product.description = request.form['description']
        product.image = request.form['image'] or product.image
        db.session.commit()
        flash('Product succesvol bijgewerkt!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('edit_product.html', product=product, business=business)

@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    if session.get('role') != 'entrepreneur':
        flash('Log in om producten te beheren.', 'error')
        return redirect(url_for('login'))

    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash('Product verwijderd.', 'success')
    return redirect(url_for('dashboard'))



@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')

        # 1. Eerst ondernemer proberen
        entrepreneur = Entrepreneur.query.filter_by(email=email).first()

        if entrepreneur and check_password_hash(entrepreneur.password, password):
            session.clear()
            session['role'] = 'entrepreneur'
            session['entrepreneur_id'] = entrepreneur.id
            session['username'] = entrepreneur.business_name

            flash("Welkom ondernemer!", "success")
            return redirect(url_for('dashboard'))

        # 2. Dan klant proberen
        customer = Customer.query.filter_by(email=email).first()

        if customer and check_password_hash(customer.password, password):
            session.clear()
            session['role'] = 'customer'
            session['customer_id'] = customer.id
            session['username'] = customer.name

            flash("Welkom terug!", "success")
            return redirect(url_for('home'))

        flash("E-mail of wachtwoord is incorrect.", "error")
        return redirect(url_for('login'))

    return render_template('login.html')



@app.route('/logout')
def logout():
    session.clear()
    flash('Je bent uitgelogd.', 'success')
    return redirect(url_for('home'))


@app.route('/')
def home():
    q = request.args.get('q', '').strip()

    # Haal ID's op van actieve ondernemers (status active of expiring_soon)
    active_subs = Subscription.query.filter(
        Subscription.status.in_(['active', 'expiring_soon'])
    ).all()
    active_owner_ids = [s.entrepreneur_id for s in active_subs]

    if q:
        businesses = Business.query.filter(
            Business.owner_id.in_(active_owner_ids),
            (Business.name.ilike(f"%{q}%")) |
            (Business.category.ilike(f"%{q}%")) |
            (Business.description.ilike(f"%{q}%"))
        ).all()
    else:
        businesses = Business.query.filter(
            Business.owner_id.in_(active_owner_ids)
        ).all()

    return render_template('home.html', businesses=businesses)


@app.route("/category/<string:category>")
def category_page(category):
    active_subs = Subscription.query.filter(
        Subscription.status.in_(['active', 'expiring_soon'])
    ).all()
    active_owner_ids = [s.entrepreneur_id for s in active_subs]

    businesses = Business.query.filter(
        Business.category == category,
        Business.owner_id.in_(active_owner_ids)
    ).all()
    return render_template("category.html", category=category, businesses=businesses)


@app.route("/business/<int:id>")
def business_page(id):
    business = Business.query.get_or_404(id)

    # verhoog business clicks en commit
    business.clicks = (business.clicks or 0) + 1
    db.session.commit()

    # producten van dit bedrijf
    products = Product.query.filter_by(business_id=business.id).all()
    return render_template("business.html", business=business, products=products)


@app.route('/dashboard')
def dashboard():
    if session.get('role') != 'entrepreneur':
        flash("Alleen ondernemers hebben toegang tot het dashboard.", "error")
        return redirect(url_for('home'))

    user = Entrepreneur.query.get(session.get('entrepreneur_id'))
    if not user:
        flash('Gebruiker niet gevonden.', 'error')
        return redirect(url_for('logout'))

    # ← NIEUW: check subscription status
    sub = check_subscription(user.id)

    # Als geen subscription: stuur naar coupon pagina
    if not sub:
        flash("Activeer je account met een couponcode om te beginnen.", "info")
        return redirect(url_for('redeem_coupon'))

    business = Business.query.filter_by(owner_id=user.id).first()

    # === HAAL NOTIFICATIES OP ===
    notifications = Notification.query.filter_by(
        entrepreneur_id=user.id,
        is_read=False
    ).order_by(Notification.timestamp.desc()).limit(5).all()


    products = Product.query.filter_by(business_id=business.id).all()

    total_products = len(products)
    total_product_clicks = sum((p.clicks or 0) for p in products)
    total_business_clicks = business.clicks or 0
    total_clicks = total_product_clicks + total_business_clicks

    reviews = Review.query.filter_by(business_id=business.id).all()

    # eenvoudige schatting van potentiële kopers
    potential_buyers = int(total_clicks * 0.25)  # 25% conversie-schatting — aanpasbaar

    return render_template(
        'dashboard.html',
        user=user,
        business=business,
        products=products,
        reviews=reviews,
        total_products=total_products,
        total_product_clicks=total_product_clicks,
        total_business_clicks=total_business_clicks,
        total_clicks=total_clicks,
        potential_buyers=potential_buyers,
        sub=sub
    )

@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)

    # verhoog product clicks
    product.clicks = (product.clicks or 0) + 1
    db.session.commit()

    # Toon een eenvoudige productpagina (kan later uitgebreid)
    return render_template("product.html", product=product)

@app.route('/signup_customer', methods=['GET', 'POST'])
def signup_customer():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        existing = Customer.query.filter_by(email=email).first()
        if existing:
            flash("E-mailadres is al gebruikt.", "error")
            return redirect(url_for('signup_customer'))

        new_customer = Customer(
            name=name,
            email=email,
            password=generate_password_hash(password)
        )
        db.session.add(new_customer)
        db.session.commit()

        flash("Account aangemaakt!", "success")
        return redirect(url_for('login'))

    return render_template("signup_customer.html")

@app.route('/wishlist')
def wishlist():
    if not require_customer():
        return redirect(url_for("login"))

    customer_id = session.get("customer_id")
    customer = Customer.query.get(customer_id)

    wishlist_items = Wishlist.query.filter_by(customer_id=customer_id).all()

    return render_template("wishlist.html", wishlist_items=wishlist_items)


@app.route('/add_review/<int:business_id>', methods=['POST'])
def add_review(business_id):
    if not require_customer():
        return redirect(url_for("login"))

    customer_id = session.get("customer_id")
    comment = request.form.get('comment', '')
    rating = int(request.form.get('rating', 5))

    review = Review(
        customer_id=customer_id,
        business_id=business_id,
        comment=comment,
        rating=rating
    )
    db.session.add(review)
    db.session.commit()

    # === NIEUWE NOTIFICATIE MAKEN ===
    business = Business.query.get(business_id)
    if business:
        notification = Notification(
            entrepreneur_id=business.owner_id,
            title="Nieuwe review",
            message=f"{rating}.8 sterren van klant op {business.name}",
            type="review"
        )
        db.session.add(notification)
        db.session.commit()

    flash("Review geplaatst!", "success")
    return redirect(url_for("business_page", id=business_id))

@app.route('/wishlist/add/<int:product_id>', methods=['POST'])
def add_wishlist(product_id):
    if not require_customer():
        return redirect(url_for("login"))

    customer_id = session.get("customer_id")

    existing = Wishlist.query.filter_by(customer_id=customer_id, product_id=product_id).first()
    if existing:
        flash("Dit item staat al in je wishlist.", "info")
        return redirect(url_for("product_detail", product_id=product_id))

    new_item = Wishlist(customer_id=customer_id, product_id=product_id)
    db.session.add(new_item)
    db.session.commit()

    # === NIEUWE NOTIFICATIE MAKEN ===
    product = Product.query.get(product_id)
    if product:
        business = Business.query.get(product.business_id)
        if business:
            notification = Notification(
                entrepreneur_id=business.owner_id,
                title="Product in wishlist",
                message=f"Iemand heeft je {product.name} toegevoegd",
                type="wishlist"
            )
            db.session.add(notification)
            db.session.commit()

    flash("Toegevoegd aan je wishlist!", "success")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route('/mark_notification_read/<int:notif_id>', methods=['POST'])
def mark_notification_read(notif_id):
    if session.get('role') != 'entrepreneur':
        return redirect(url_for('login'))

    notification = Notification.query.get_or_404(notif_id)

    # alleen de eigenaar mag het lezen
    if notification.entrepreneur_id == session['entrepreneur_id']:
        notification.is_read = True
        db.session.commit()

    return redirect(url_for('dashboard'))

@app.route('/wishlist/remove/<int:item_id>', methods=['POST'])
def remove_wishlist(item_id):
    if not require_customer():
        return redirect(url_for("login"))

    item = Wishlist.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()

    flash("Item verwijderd uit je wishlist.", "success")
    return redirect(url_for("wishlist"))

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/redeem_coupon', methods=['GET', 'POST'])
def redeem_coupon():
    if session.get('role') != 'entrepreneur':
        return redirect(url_for('login'))

    user = Entrepreneur.query.get(session['entrepreneur_id'])

    if request.method == 'POST':
        code = request.form.get('coupon_code', '').strip().upper()

        # Zoek de coupon in de database
        coupon = Coupon.query.filter_by(code=code, is_used=False).first()

        if not coupon:
            flash("Ongeldige of al gebruikte couponcode.", "error")
            return redirect(url_for('redeem_coupon'))

        # Markeer coupon als gebruikt
        coupon.is_used = True
        coupon.used_by = user.id
        coupon.used_at = datetime.utcnow()

        # Maak of vernieuw de subscription
        sub = Subscription.query.filter_by(entrepreneur_id=user.id).first()
        now = datetime.utcnow()

        if sub:
            # Verlenging: reset de timer
            sub.activated_at = now
            sub.expires_at = now + timedelta(days=30)
            sub.status = 'active'
        else:
            # Eerste keer activeren
            sub = Subscription(
                entrepreneur_id=user.id,
                activated_at=now,
                expires_at=now + timedelta(days=30),
                status='active'
            )
            db.session.add(sub)

        db.session.commit()
        flash("✅ Account geactiveerd! Je abonnement loopt 30 dagen.", "success")
        return redirect(url_for('dashboard'))

    # Haal huidige subscription op om te tonen
    sub = Subscription.query.filter_by(entrepreneur_id=user.id).first()
    return render_template('redeem_coupon.html', sub=sub)

@app.route('/admin/create_coupon/<code>')
def create_coupon(code):
    # Simpele beveiliging: verander deze sleutel
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY', '3f3z13rs33nv13r3ph3s14ns4002562951413'):
        return "Geen toegang", 403

    existing = Coupon.query.filter_by(code=code.upper()).first()
    if existing:
        return f"Code {code} bestaat al!"
    coupon = Coupon(code=code.upper())
    db.session.add(coupon)
    db.session.commit()
    return f"✅ Coupon '{code.upper()}' aangemaakt!"

@app.route('/admin/generate_coupon')
def generate_coupon():
    admin_key = request.args.get('key', '')
    if admin_key != os.environ.get('ADMIN_KEY'):
        return "Geen toegang", 403

    # Genereer een willekeurige code
    random_code = "BIZZLY-" + uuid.uuid4().hex[:8].upper()

    # Sla op in database
    existing = Coupon.query.filter_by(code=random_code).first()
    if existing:
        return "Probeer opnieuw, code bestond al!"

    coupon = Coupon(code=random_code)
    db.session.add(coupon)
    db.session.commit()
    return f"✅ Coupon aangemaakt: {random_code}"

@app.route('/update_facebook', methods=['POST'])
def update_facebook():
    if session.get('role') != 'entrepreneur':
        return redirect(url_for('login'))

    user = Entrepreneur.query.get(session['entrepreneur_id'])
    business = Business.query.filter_by(owner_id=user.id).first()

    business.facebook_url = request.form.get('facebook_url', '').strip() or None
    db.session.commit()

    flash("Facebook pagina opgeslagen!", "success")
    return redirect(url_for('dashboard'))

if __name__ == "__main__":
    app.run(debug=False)

