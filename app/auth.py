"""
Authentication routes and handlers
"""
from datetime import datetime

from flask import Blueprint, current_app, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.i18n import t as _t
from app.login_guard import LoginThrottle, client_ip, retry_phrase, safe_next_url
from app.models import User
from app.utils import super_admin_required, validate_username

auth_bp = Blueprint('auth', __name__)
_login_throttle = LoginThrottle()


def _client_ip() -> str:
    return client_ip(
        request.remote_addr,
        request.headers.get('X-Real-IP'),
        request.headers.get('X-Forwarded-For'),
        current_app.config.get('TRUSTED_PROXIES'),
    )


def _after_login_url() -> str:
    return safe_next_url(request.args.get('next')) or url_for('dashboard.index')


@auth_bp.route('/')
def index():
    """Redirect to dashboard or login"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    return redirect(url_for('auth.login'))

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    nxt = safe_next_url(request.args.get('next'))
    if current_user.is_authenticated:
        return redirect(nxt or url_for('dashboard.index'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        ip = _client_ip()
        wait = _login_throttle.retry_after(ip, username)
        if wait:
            flash(_t('Too many login attempts. Try again in {wait}.',
                     wait=retry_phrase(wait, _t)), 'danger')
            return render_template('login.html', login_next=nxt), 429

        user = User.query.filter_by(username=username).first() if username else None

        # A disabled account gets the same message as a wrong password.
        if user and user.is_active and user.check_password(password):
            _login_throttle.record_success(username)
            login_user(user, remember=True)
            user.last_login_at = datetime.utcnow()
            user.last_login_method = 'password'
            db.session.commit()
            return redirect(_after_login_url())

        _login_throttle.record_failure(ip, username)
        flash(_t('Invalid username or password'), 'danger')

    return render_template('login.html', login_next=nxt)

@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    flash(_t('You have been logged out successfully'), 'success')
    return redirect(url_for('auth.login'))

@auth_bp.route('/register', methods=['GET', 'POST'])
@login_required
@super_admin_required
def register():
    """Register a new user without subject access (super admin only;
    Admin → Users is the main path)."""
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        
        ok, err = validate_username(username)
        if not ok:
            # validate_username's messages are catalogue keys
            flash(_t(err), 'danger')
        elif User.query.filter_by(username=username).first():
            flash(_t('Username already exists'), 'danger')
        elif not password:
            flash(_t('Username and password are required'), 'danger')
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(_t('User {username} created successfully', username=username), 'success')
            return redirect(url_for('dashboard.index'))
    
    return render_template('register.html')
