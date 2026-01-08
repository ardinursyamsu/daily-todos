from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import sqlite3
import os
from dotenv import load_dotenv

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-change-this')
DATABASE = 'todos.db'

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# User model
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user_data = conn.execute('SELECT id, username, password_hash FROM users WHERE id = ?', (int(user_id),)).fetchone()
    conn.close()
    if user_data:
        return User(user_data['id'], user_data['username'], user_data['password_hash'])
    return None

def init_db():
    """Initialize the database with required tables."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create todos table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            deadline DATE,
            created_date DATE DEFAULT CURRENT_DATE,
            completed BOOLEAN DEFAULT 0,
            parent_id INTEGER DEFAULT NULL,
            user_id INTEGER NOT NULL,
            FOREIGN KEY (parent_id) REFERENCES todos (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Insert example data if table is empty
    cursor.execute('SELECT COUNT(*) FROM todos')
    if cursor.fetchone()[0] == 0:
        # Create default user if none exists
        cursor.execute('SELECT COUNT(*) FROM users')
        if cursor.fetchone()[0] == 0:
            default_password_hash = generate_password_hash('password')  # Change this in production
            cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                          ('admin', default_password_hash))

        # Get the default user ID
        default_user = cursor.execute('SELECT id FROM users WHERE username = ?', ('admin',)).fetchone()
        if default_user:
            cursor.execute('''
                INSERT INTO todos (title, description, deadline, created_date, completed, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ('Sample Task', 'This is a sample task description', date.today(), date.today(), 0, default_user[0]))

    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # This enables column access by name
    return conn

@app.route('/')
@login_required
def index():
    """Main route to display the todo list."""
    today = date.today()
    conn = get_db_connection()

    # Get today's incomplete todos (both main tasks and subtasks) for current user
    incomplete_todos = conn.execute('''
        SELECT t.id, t.title, t.description, t.deadline, t.completed, t.parent_id,
               (SELECT COUNT(*) FROM todos st WHERE st.parent_id = t.id) as subtask_count
        FROM todos t
        WHERE DATE(t.created_date) = ? AND t.parent_id IS NULL AND t.completed = 0 AND t.user_id = ?
        ORDER BY t.id
    ''', (today, current_user.id))
    incomplete_todos = [dict(row) for row in incomplete_todos.fetchall()]

    # Get today's completed todos (both main tasks and subtasks) for current user
    completed_todos = conn.execute('''
        SELECT t.id, t.title, t.description, t.deadline, t.completed, t.parent_id,
               (SELECT COUNT(*) FROM todos st WHERE st.parent_id = t.id) as subtask_count
        FROM todos t
        WHERE DATE(t.created_date) = ? AND t.parent_id IS NULL AND t.completed = 1 AND t.user_id = ?
        ORDER BY t.id
    ''', (today, current_user.id))
    completed_todos = [dict(row) for row in completed_todos.fetchall()]

    # Get subtasks for each main task (for incomplete todos)
    for todo in incomplete_todos:
        subtasks = conn.execute('''
            SELECT st.id, st.title, st.completed
            FROM todos st
            WHERE st.parent_id = ? AND st.user_id = ?
            ORDER BY st.id
        ''', (todo['id'], current_user.id))
        todo['subtasks'] = [dict(row) for row in subtasks.fetchall()]

    # Get subtasks for each main task (for completed todos)
    for todo in completed_todos:
        subtasks = conn.execute('''
            SELECT st.id, st.title, st.completed
            FROM todos st
            WHERE st.parent_id = ? AND st.user_id = ?
            ORDER BY st.id
        ''', (todo['id'], current_user.id))
        todo['subtasks'] = [dict(row) for row in subtasks.fetchall()]

    # Check for unfinished tasks from previous days that need to be carried over for current user
    carry_over_todos = get_unfinished_from_previous_days(today, current_user.id)

    conn.close()

    return render_template('index.html', incomplete_todos=incomplete_todos, completed_todos=completed_todos, carry_over_todos=carry_over_todos)

def get_unfinished_from_previous_days(today, user_id):
    """Get unfinished tasks from previous days that need to be carried over."""
    conn = get_db_connection()
    # Find all incomplete tasks from previous days
    todos = conn.execute('''
        SELECT t.id, t.title, t.description, t.deadline, t.created_date,
               (SELECT COUNT(*) FROM todos st WHERE st.parent_id = t.id) as subtask_count
        FROM todos t
        WHERE DATE(t.created_date) < ? AND t.completed = 0 AND t.parent_id IS NULL AND t.user_id = ?
        ORDER BY t.id
    ''', (today, user_id))
    result = [dict(row) for row in todos.fetchall()]
    conn.close()
    return result

@app.route('/add_todo', methods=['POST'])
@login_required
def add_todo():
    """Add a new todo item."""
    data = request.get_json()

    title = data.get('title')
    description = data.get('description', '')
    deadline_str = data.get('deadline', None)
    parent_id = data.get('parent_id', None)

    if not title:
        return jsonify({'error': 'Title is required'}), 400

    deadline = None
    if deadline_str:
        try:
            deadline = datetime.strptime(deadline_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400

    conn = get_db_connection()

    # Insert the new todo with user_id
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO todos (title, description, deadline, parent_id, user_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (title, description, deadline, parent_id, current_user.id))

    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Return the newly created todo
    return jsonify({
        'id': new_id,
        'title': title,
        'description': description,
        'deadline': deadline_str,
        'parent_id': parent_id,
        'completed': 0
    })

@app.route('/toggle_complete/<int:todo_id>', methods=['POST'])
@login_required
def toggle_complete(todo_id):
    """Toggle the completion status of a todo."""
    conn = get_db_connection()
    # Check if the todo belongs to the current user
    todo = conn.execute('SELECT completed FROM todos WHERE id = ? AND user_id = ?', (todo_id, current_user.id)).fetchone()

    if todo:
        new_status = 0 if todo['completed'] else 1
        conn.execute('UPDATE todos SET completed = ? WHERE id = ?', (new_status, todo_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'completed': bool(new_status)})
    else:
        conn.close()
        return jsonify({'error': 'Todo not found'}), 404

@app.route('/carry_over', methods=['POST'])
@login_required
def carry_over():
    """Carry over unfinished tasks from previous days to today."""
    data = request.get_json()
    todo_ids = data.get('todo_ids', [])

    today = date.today()
    conn = get_db_connection()

    # Update the created_date for selected todos to today (only for current user's todos)
    for todo_id in todo_ids:
        # Verify that the todo belongs to the current user
        todo = conn.execute('SELECT id FROM todos WHERE id = ? AND user_id = ?', (todo_id, current_user.id)).fetchone()
        if todo:
            conn.execute('UPDATE todos SET created_date = ? WHERE id = ?', (today, todo_id))
            # Also update subtasks if any (for current user)
            conn.execute('UPDATE todos SET created_date = ? WHERE parent_id = ? AND user_id = ?', (today, todo_id, current_user.id))

    conn.commit()
    conn.close()

    return jsonify({'success': True})

@app.route('/delete_todo/<int:todo_id>', methods=['DELETE'])
@login_required
def delete_todo(todo_id):
    """Delete a todo item."""
    conn = get_db_connection()

    # Delete subtasks first (if any) - only for current user
    conn.execute('DELETE FROM todos WHERE parent_id = ? AND user_id = ?', (todo_id, current_user.id))

    # Delete the main todo - only if it belongs to current user
    result = conn.execute('DELETE FROM todos WHERE id = ? AND user_id = ?', (todo_id, current_user.id))

    conn.commit()
    conn.close()

    if result.rowcount > 0:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Todo not found'}), 404

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        user_data = conn.execute('SELECT id, username, password_hash FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        if user_data and check_password_hash(user_data['password_hash'], password):
            user = User(user_data['id'], user_data['username'], user_data['password_hash'])
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Invalid username or password')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Check if user already exists
        conn = get_db_connection()
        existing_user = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()

        if existing_user:
            flash('Username already exists')
            conn.close()
            return render_template('register.html')

        # Create new user
        password_hash = generate_password_hash(password)
        conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, password_hash))
        conn.commit()
        conn.close()

        flash('Registration successful. Please log in.')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    # Load environment variables from .env file
    load_dotenv()

    # Get configuration from environment variables with defaults
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

    init_db()
    app.run(host=host, port=port, debug=debug)