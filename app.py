from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
import qrcode
from io import BytesIO
import base64
import uuid
from datetime import datetime
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tu_clave_secreta_aqui'  # Cambiar en producción
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///amigo_invisible.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Gift(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)
    owner_session_id = db.Column(db.String(36), nullable=False)
    participants = db.relationship('Participant', backref='gift', lazy=True)

class Participant(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    gift_id = db.Column(db.String(36), db.ForeignKey('gift.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    session_id = db.Column(db.String(36), nullable=False)
    assigned_to_id = db.Column(db.String(36), db.ForeignKey('participant.id'))

def generate_qr(url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

@app.route('/gifts/<gift_id>/status', methods=['GET'])
def get_gift_status(gift_id):
    gift = Gift.query.get_or_404(gift_id)
    return jsonify({
        'status': gift.status,
        'total_participants': len(gift.participants)
    })

@app.route('/gifts/<gift_id>/participants', methods=['GET'])
def get_participants(gift_id):
    gift = Gift.query.get_or_404(gift_id)
    participants = [{'name': p.name} for p in gift.participants]
    return jsonify(participants)

def assign_gifts(participants):
    givers = participants.copy()
    receivers = participants.copy()
    random.shuffle(receivers)
    
    # Evitar auto-asignaciones
    while any(g.id == r.id for g, r in zip(givers, receivers)):
        random.shuffle(receivers)
    
    # Guardar asignaciones
    for giver, receiver in zip(givers, receivers):
        giver.assigned_to_id = receiver.id
    
    return True

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/gifts', methods=['POST'])
def create_gift():
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    
    gift = Gift(
        id=str(uuid.uuid4()),
        name=request.form['name'],
        owner_session_id=session['session_id']
    )
    db.session.add(gift)
    db.session.commit()
    
    gift_url = url_for('view_gift', gift_id=gift.id, _external=True)
    qr_code = generate_qr(gift_url)
    
    return render_template('gift_created.html', gift=gift, qr_code=qr_code, gift_url=gift_url)

@app.route('/gifts/<gift_id>', methods=['GET'])
def view_gift(gift_id):
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    
    gift = Gift.query.get_or_404(gift_id)
    is_owner = gift.owner_session_id == session['session_id']
    
    participant = Participant.query.filter_by(
        gift_id=gift_id,
        session_id=session['session_id']
    ).first()
    
    if gift.status == 'closed' and participant:
        assigned_to = Participant.query.get(participant.assigned_to_id)
        return render_template('gift_assignment.html', 
                             gift=gift, 
                             participant=participant,
                             assigned_to=assigned_to)
    
    return render_template('gift_join.html', 
                         gift=gift, 
                         is_owner=is_owner,
                         participant=participant)

@app.route('/gifts/<gift_id>/join', methods=['POST'])
def join_gift(gift_id):
    try:
        app.logger.info(f"Intento de unirse al sorteo {gift_id}")
        app.logger.info(f"Headers recibidos: {dict(request.headers)}")
        app.logger.info(f"Datos del formulario: {dict(request.form)}")
        
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
            app.logger.info(f"Nueva session_id creada: {session['session_id']}")
        else:
            app.logger.info(f"Session_id existente: {session['session_id']}")
        
        gift = Gift.query.get_or_404(gift_id)
        app.logger.info(f"Sorteo encontrado: {gift.name}")
        
        if gift.status != 'open':
            app.logger.warning(f"Intento de unirse a sorteo cerrado: {gift_id}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'El sorteo ya está cerrado'}), 400
            return redirect(url_for('view_gift', gift_id=gift_id))
        
        # Verificar si ya está participando
        existing = Participant.query.filter_by(
            gift_id=gift_id,
            session_id=session['session_id']
        ).first()
        
        if existing:
            app.logger.info(f"Participante ya existe: {existing.name}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': True, 'message': 'Ya estás participando'})
            return redirect(url_for('view_gift', gift_id=gift_id))
        
        name = request.form.get('name')
        if not name:
            app.logger.error("No se recibió nombre en el formulario")
            return jsonify({'error': 'El nombre es requerido'}), 400
        
        participant = Participant(
            id=str(uuid.uuid4()),
            gift_id=gift_id,
            name=name,
            session_id=session['session_id']
        )
        db.session.add(participant)
        db.session.commit()
        app.logger.info(f"Nuevo participante añadido: {name}")
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': 'Te has unido al sorteo correctamente'})
        return redirect(url_for('view_gift', gift_id=gift_id))
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error en join_gift: {str(e)}")
        app.logger.exception("Stacktrace completo:")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(e)}), 500
        return redirect(url_for('view_gift', gift_id=gift_id))

@app.route('/gifts/<gift_id>/close', methods=['POST'])
def close_gift(gift_id):
    gift = Gift.query.get_or_404(gift_id)
    
    if gift.owner_session_id != session['session_id']:
        return redirect(url_for('view_gift', gift_id=gift_id))
    
    if gift.status == 'open':
        participants = gift.participants
        if len(participants) >= 2:
            assign_gifts(participants)
            gift.status = 'closed'
            gift.closed_at = datetime.utcnow()
            db.session.commit()
    
    return redirect(url_for('view_gift', gift_id=gift_id))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
