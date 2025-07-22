from flask import Flask, render_template, request, redirect, session, url_for
import boto3, os, uuid

# === AWS Configuration ===
AWS_REGION = 'us-east-1'
USERS_TABLE = 'user'
PRODUCTS_TABLE = 'product'
ORDERS_TABLE = 'order'
SERVICES_TABLE = 'service'
SNS_TOPIC_ARN = "your_sns_topic_arn_here"

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
sns_client = boto3.client('sns', region_name=AWS_REGION)
users_table = dynamodb.Table(USERS_TABLE)
products_table = dynamodb.Table(PRODUCTS_TABLE)
orders_table = dynamodb.Table(ORDERS_TABLE)
services_table = dynamodb.Table(SERVICES_TABLE)

app = Flask(__name__)
app.secret_key = 'pickle_secret'

# === Init Admin User ===
def init_admin():
    try:
        res = users_table.get_item(Key={'username': 'admin'})
        if 'Item' not in res:
            users_table.put_item(Item={
                'username': 'admin',
                'password': 'admin123',
                'is_admin': True
            })
            print("✅ Admin user created.")
    except Exception as e:
        print(f"❌ Error initializing admin: {e}")

@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        response = products_table.scan()
        products = response.get('Items', [])
        return render_template('index.html', products=products)
    except Exception as e:
        return f"Error loading products: {e}"

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        try:
            users_table.put_item(Item={
                'username': username,
                'password': password,
                'is_admin': False
            }, ConditionExpression='attribute_not_exists(username)')
            return redirect(url_for('login'))
        except Exception:
            return "Username already exists"
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        res = users_table.get_item(Key={'username': username})
        user = res.get('Item')
        if user and user['password'] == password:
            session['user_id'] = username
            session['username'] = username
            return redirect(url_for('home'))
        return "Invalid credentials"
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/add_to_cart/<string:pid>')
def add_to_cart(pid):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    cart = session.get('cart', {})
    cart[pid] = cart.get(pid, 0) + 1
    session['cart'] = cart
    return redirect(url_for('cart'))

@app.route('/cart')
def cart():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    items, total = [], 0
    for pid, qty in session.get('cart', {}).items():
        product = products_table.get_item(Key={'product_id': pid}).get('Item')
        if product:
            items.append((product, qty))
            total += float(product['price']) * qty
    return render_template('cart.html', items=items, total=total)

@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    cart = session.get('cart', {})
    payment_method = request.form.get('payment_method')
    username = session['username']
    order_summary = []

    for pid, qty in cart.items():
        product = products_table.get_item(Key={'product_id': pid}).get('Item')
        if not product:
            continue
        order_id = str(uuid.uuid4())
        orders_table.put_item(Item={
            'order_id': order_id,
            'username': username,
            'product_id': pid,
            'quantity': qty,
            'status': f'Confirmed ({payment_method})'
        })
        products_table.update_item(
            Key={'product_id': pid},
            UpdateExpression="SET quantity = quantity - :val",
            ExpressionAttributeValues={':val': qty}
        )
        order_summary.append(f"{product['name']} x{qty}")

    session['cart'] = {}

    # ✅ SNS notification
    try:
        message = f"Hi {username}, your order is confirmed:\n" + "\n".join(order_summary)
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject='Order Confirmation'
        )
    except Exception as e:
        print("SNS Error:", e)

    return render_template('success.html')

# === Admin Routes ===

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = users_table.get_item(Key={'username': username}).get('Item')
        if user and user.get('password') == password and user.get('is_admin'):
            session['admin_id'] = username
            return redirect(url_for('admin_dashboard'))
        return "Invalid admin credentials"
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    return render_template('admin_dashboard.html')

@app.route('/admin/add', methods=['GET', 'POST'])
def admin_add_product():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        pid = str(uuid.uuid4())
        product = {
            'product_id': pid,
            'name': request.form['name'],
            'price': float(request.form['price']),
            'quantity': int(request.form['quantity']),
            'image': request.form['image'] or '/static/images/pickle1.jpg'
        }
        products_table.put_item(Item=product)
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_add.html')

@app.route('/admin/stock')
def admin_stock():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    products = products_table.scan().get('Items', [])
    return render_template('admin_stock.html', products=products)

@app.route('/admin/orders')
def admin_orders():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    orders = orders_table.scan().get('Items', [])
    return render_template('admin_orders.html', orders=orders)

# === Service Request (optional) ===
@app.route('/service-request', methods=['GET', 'POST'])
def service_request():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        service = {
            'service_id': str(uuid.uuid4()),
            'username': session['username'],
            'type': request.form['type'],
            'description': request.form['description'],
            'status': 'Pending'
        }
        services_table.put_item(Item=service)
        return "Service request submitted!"
    return render_template('service_form.html')

# === App Runner ===
if __name__ == "__main__":
    init_admin()
    app.run(host="0.0.0.0", port=5000, debug=True)
