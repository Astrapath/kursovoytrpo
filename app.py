from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import datetime

app = Flask(__name__)
app.secret_key = 'spo_system_secret_key'

ROLES = ['администратор', 'преподаватель']
GRADE_TYPES = ['текущая', 'промежуточная', 'итоговая']



def db():
    conn = sqlite3.connect('spo.db')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def logged_in():
    return 'user_id' in session


def has_role(*roles):
    return logged_in() and session.get('role') in roles


def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fio TEXT NOT NULL,
        login TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('администратор','преподаватель')),
        active INTEGER NOT NULL DEFAULT 1,
        created TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        specialty TEXT
    );
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fio TEXT NOT NULL,
        group_id INTEGER NOT NULL REFERENCES groups(id)
    );
    CREATE TABLE IF NOT EXISTS disciplines(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        teacher_id INTEGER NOT NULL REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS grades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL REFERENCES students(id),
        discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
        teacher_id INTEGER NOT NULL REFERENCES users(id),
        grade INTEGER NOT NULL CHECK(grade BETWEEN 2 AND 5),
        grade_type TEXT NOT NULL,
        semester INTEGER NOT NULL,
        date TEXT NOT NULL
    );
    """)

    if conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        conn.execute(
            'INSERT INTO users(fio,login,password_hash,role,created) VALUES(?,?,?,?,?)',
            ('Администратор', 'admin',
             generate_password_hash('admin123'), 'администратор', now()))
        conn.execute(
            'INSERT INTO users(fio,login,password_hash,role,created) VALUES(?,?,?,?,?)',
            ('Иванов Иван Иванович', 'teacher',
             generate_password_hash('teacher123'), 'преподаватель', now()))
        conn.execute("INSERT INTO groups(name,specialty) VALUES('ИС-21','Информационные системы и программирование')")
        conn.execute("INSERT INTO groups(name,specialty) VALUES('ИС-22','Информационные системы и программирование')")
        conn.execute("INSERT INTO students(fio,group_id) VALUES('Петров Пётр Петрович',1)")
        conn.execute("INSERT INTO students(fio,group_id) VALUES('Сидорова Анна Сергеевна',1)")
        conn.execute("INSERT INTO students(fio,group_id) VALUES('Кузнецов Дмитрий Олегович',2)")
        conn.execute("INSERT INTO disciplines(name,teacher_id) VALUES('Базы данных',2)")
        conn.execute("INSERT INTO disciplines(name,teacher_id) VALUES('Веб-программирование',2)")
        conn.commit()
    conn.close()



@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_val = request.form['login'].strip()
        password = request.form['password']
        conn = db()
        user = conn.execute('SELECT * FROM users WHERE login=?', (login_val,)).fetchone()
        conn.close()
        if user is None or not check_password_hash(user['password_hash'], password):
            flash('Неверный логин или пароль')
        elif user['active'] == 0:
            flash('Учётная запись заблокирована')
        else:
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['fio'] = user['fio']
            if user['role'] == 'администратор':
                return redirect('/admin/reports')
            return redirect('/teacher/disciplines')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/')
def index():
    if not logged_in():
        return redirect('/login')
    if session['role'] == 'администратор':
        return redirect('/admin/reports')
    return redirect('/teacher/disciplines')



@app.route('/teacher/disciplines')
def teacher_disciplines():
    if not has_role('преподаватель'):
        return redirect('/login')
    conn = db()
    disciplines = conn.execute('''
        SELECT d.*, (SELECT COUNT(*) FROM grades g WHERE g.discipline_id=d.id) AS grades_count
        FROM disciplines d WHERE d.teacher_id=? ORDER BY d.name
    ''', (session['user_id'],)).fetchall()
    conn.close()
    return render_template('teacher_disciplines.html', disciplines=disciplines)


@app.route('/teacher/discipline/<int:did>')
def teacher_discipline(did):
    if not has_role('преподаватель'):
        return redirect('/login')
    conn = db()
    d = conn.execute('SELECT * FROM disciplines WHERE id=? AND teacher_id=?',
                     (did, session['user_id'])).fetchone()
    if d is None:
        conn.close()
        flash('Дисциплина не найдена или не принадлежит вам')
        return redirect('/teacher/disciplines')

    students = conn.execute('''
        SELECT s.*, g.name AS group_name FROM students s
        JOIN groups g ON g.id=s.group_id ORDER BY g.name, s.fio
    ''').fetchall()

    avg = conn.execute('''
        SELECT student_id, ROUND(AVG(grade),2) AS avg_grade FROM grades
        WHERE discipline_id=? GROUP BY student_id
    ''', (did,)).fetchall()
    avg_map = {a['student_id']: a['avg_grade'] for a in avg}

    grades = conn.execute('''
        SELECT g.*, s.fio AS student_fio FROM grades g
        JOIN students s ON s.id=g.student_id
        WHERE g.discipline_id=? AND g.teacher_id=?
        ORDER BY g.date DESC
    ''', (did, session['user_id'])).fetchall()
    conn.close()
    return render_template('teacher_discipline.html', d=d, students=students,
                           grades=grades, avg_map=avg_map, types=GRADE_TYPES)


@app.route('/teacher/grade/add', methods=['POST'])
def teacher_grade_add():
    if not has_role('преподаватель'):
        return redirect('/login')
    did = int(request.form['discipline_id'])
    sid = int(request.form['student_id'])
    gtype = request.form['grade_type']
    semester = request.form['semester']

    conn = db()
    d = conn.execute('SELECT * FROM disciplines WHERE id=? AND teacher_id=?',
                     (did, session['user_id'])).fetchone()
    if d is None:
        conn.close()
        flash('Нельзя выставить оценку по чужой дисциплине')
        return redirect('/teacher/disciplines')

    try:
        g = int(request.form['grade'])
    except ValueError:
        conn.close()
        flash('Оценка должна быть числом от 2 до 5')
        return redirect(f'/teacher/discipline/{did}')
    if g < 2 or g > 5:
        conn.close()
        flash('Оценка должна быть от 2 до 5')
        return redirect(f'/teacher/discipline/{did}')
    try:
        semester = int(semester)
    except ValueError:
        semester = 1

    conn.execute('''INSERT INTO grades(student_id,discipline_id,teacher_id,grade,grade_type,semester,date)
                    VALUES(?,?,?,?,?,?,?)''',
                 (sid, did, session['user_id'], g, gtype, semester, now()))
    conn.commit()
    conn.close()
    flash('Оценка выставлена')
    return redirect(f'/teacher/discipline/{did}')


@app.route('/teacher/grade/edit/<int:gid>', methods=['POST'])
def teacher_grade_edit(gid):
    if not has_role('преподаватель'):
        return redirect('/login')
    conn = db()
    grade = conn.execute('SELECT * FROM grades WHERE id=? AND teacher_id=?',
                         (gid, session['user_id'])).fetchone()
    if grade is None:
        conn.close()
        flash('Оценка не найдена или выставлена другим преподавателем')
        return redirect('/teacher/disciplines')
    try:
        g = int(request.form['grade'])
    except ValueError:
        conn.close()
        flash('Оценка должна быть числом от 2 до 5')
        return redirect(f'/teacher/discipline/{grade["discipline_id"]}')
    if g < 2 or g > 5:
        conn.close()
        flash('Оценка должна быть от 2 до 5')
        return redirect(f'/teacher/discipline/{grade["discipline_id"]}')
    conn.execute('UPDATE grades SET grade=? WHERE id=?', (g, gid))
    conn.commit()
    conn.close()
    flash('Оценка изменена')
    return redirect(f'/teacher/discipline/{grade["discipline_id"]}')


@app.route('/teacher/grade/delete/<int:gid>')
def teacher_grade_delete(gid):
    if not has_role('преподаватель'):
        return redirect('/login')
    conn = db()
    grade = conn.execute('SELECT * FROM grades WHERE id=? AND teacher_id=?',
                         (gid, session['user_id'])).fetchone()
    if grade is None:
        conn.close()
        flash('Оценка не найдена или выставлена другим преподавателем')
        return redirect('/teacher/disciplines')
    conn.execute('DELETE FROM grades WHERE id=?', (gid,))
    conn.commit()
    conn.close()
    flash('Оценка удалена')
    return redirect(f'/teacher/discipline/{grade["discipline_id"]}')



@app.route('/admin/groups', methods=['GET', 'POST'])
def admin_groups():
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if request.method == 'POST':
        name = request.form['name'].strip()
        spec = request.form.get('specialty', '').strip()
        if name == '':
            flash('Введите название группы')
        elif conn.execute('SELECT id FROM groups WHERE name=?', (name,)).fetchone():
            flash('Группа с таким названием уже существует')
        else:
            conn.execute('INSERT INTO groups(name,specialty) VALUES(?,?)', (name, spec))
            conn.commit()
            flash('Группа добавлена')
    groups = conn.execute('SELECT * FROM groups ORDER BY name').fetchall()
    conn.close()
    return render_template('admin_groups.html', groups=groups)


@app.route('/admin/groups/delete/<int:gid>')
def delete_group(gid):
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if conn.execute('SELECT id FROM students WHERE group_id=?', (gid,)).fetchone():
        flash('Нельзя удалить группу, в которой есть студенты')
    else:
        conn.execute('DELETE FROM groups WHERE id=?', (gid,))
        conn.commit()
        flash('Группа удалена')
    conn.close()
    return redirect('/admin/groups')



@app.route('/admin/students', methods=['GET', 'POST'])
def admin_students():
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if request.method == 'POST':
        fio = request.form['fio'].strip()
        gid = request.form.get('group_id', '')
        if fio == '' or gid == '':
            flash('Заполните ФИО и выберите группу')
        else:
            conn.execute('INSERT INTO students(fio,group_id) VALUES(?,?)', (fio, gid))
            conn.commit()
            flash('Студент добавлен')

    students = conn.execute('''
        SELECT s.*, g.name AS group_name FROM students s
        JOIN groups g ON g.id=s.group_id ORDER BY g.name, s.fio
    ''').fetchall()
    groups = conn.execute('SELECT * FROM groups ORDER BY name').fetchall()
    conn.close()
    return render_template('admin_students.html', students=students, groups=groups)


@app.route('/admin/students/delete/<int:sid>')
def delete_student(sid):
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if conn.execute('SELECT id FROM grades WHERE student_id=?', (sid,)).fetchone():
        flash('Нельзя удалить студента, у которого есть оценки')
    else:
        conn.execute('DELETE FROM students WHERE id=?', (sid,))
        conn.commit()
        flash('Студент удалён')
    conn.close()
    return redirect('/admin/students')



@app.route('/admin/disciplines', methods=['GET', 'POST'])
def admin_disciplines():
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if request.method == 'POST':
        name = request.form['name'].strip()
        tid = request.form.get('teacher_id', '')
        if name == '' or tid == '':
            flash('Заполните название и выберите преподавателя')
        else:
            conn.execute('INSERT INTO disciplines(name,teacher_id) VALUES(?,?)', (name, tid))
            conn.commit()
            flash('Дисциплина добавлена')

    disciplines = conn.execute('''
        SELECT d.*, u.fio AS teacher_fio FROM disciplines d
        JOIN users u ON u.id=d.teacher_id ORDER BY d.name
    ''').fetchall()
    teachers = conn.execute("SELECT * FROM users WHERE role='преподаватель' ORDER BY fio").fetchall()
    conn.close()
    return render_template('admin_disciplines.html', disciplines=disciplines, teachers=teachers)


@app.route('/admin/disciplines/delete/<int:did>')
def delete_discipline(did):
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if conn.execute('SELECT id FROM grades WHERE discipline_id=?', (did,)).fetchone():
        flash('Нельзя удалить дисциплину, по которой есть оценки')
    else:
        conn.execute('DELETE FROM disciplines WHERE id=?', (did,))
        conn.commit()
        flash('Дисциплина удалена')
    conn.close()
    return redirect('/admin/disciplines')



@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    if request.method == 'POST':
        fio = request.form['fio'].strip()
        login_val = request.form['login'].strip()
        password = request.form['password']
        role = request.form['role']

        if fio == '' or login_val == '' or password == '':
            flash('Заполните все поля')
        elif conn.execute('SELECT id FROM users WHERE login=?', (login_val,)).fetchone():
            flash('Такой логин уже используется')
        else:
            conn.execute('INSERT INTO users(fio,login,password_hash,role,created) VALUES(?,?,?,?,?)',
                         (fio, login_val, generate_password_hash(password), role, now()))
            conn.commit()
            flash('Пользователь создан')
    users = conn.execute('SELECT * FROM users ORDER BY id').fetchall()
    conn.close()
    return render_template('admin_users.html', users=users, roles=ROLES)


@app.route('/admin/users/toggle/<int:uid>')
def toggle_user(uid):
    if not has_role('администратор'):
        return redirect('/login')
    if uid == session['user_id']:
        flash('Нельзя заблокировать свою учётную запись')
        return redirect('/admin/users')
    conn = db()
    u = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if u is not None:
        new_state = 0 if u['active'] else 1
        conn.execute('UPDATE users SET active=? WHERE id=?', (new_state, uid))
        conn.commit()
        flash('Пользователь заблокирован' if new_state == 0 else 'Пользователь разблокирован')
    conn.close()
    return redirect('/admin/users')



@app.route('/admin/reports')
def admin_reports():
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    groups = conn.execute('SELECT * FROM groups ORDER BY name').fetchall()
    conn.close()
    return render_template('admin_reports.html', groups=groups)


@app.route('/admin/report/summary')
def summary_report():
    if not has_role('администратор'):
        return redirect('/login')
    gid = request.args.get('group_id', '')
    sem = request.args.get('semester', '')

    conn = db()
    if gid == '':
        conn.close()
        flash('Выберите группу')
        return redirect('/admin/reports')

    group = conn.execute('SELECT * FROM groups WHERE id=?', (gid,)).fetchone()
    students = conn.execute('SELECT * FROM students WHERE group_id=? ORDER BY fio', (gid,)).fetchall()
    disciplines = conn.execute('SELECT * FROM disciplines ORDER BY name').fetchall()

    rows = []
    for s in students:
        cells = []
        total = 0
        cnt = 0
        for d in disciplines:
            q = 'SELECT grade FROM grades WHERE student_id=? AND discipline_id=?'
            params = [s['id'], d['id']]
            if sem:
                q += ' AND semester=?'
                params.append(sem)
            g = conn.execute(q, params).fetchone()
            val = g['grade'] if g else ''
            cells.append(val)
            if g:
                total += g['grade']
                cnt += 1
        avg = round(total / cnt, 2) if cnt else '—'
        rows.append({'fio': s['fio'], 'cells': cells, 'avg': avg})
    conn.close()
    return render_template('summary_report.html', rows=rows, disciplines=disciplines,
                           group=group, sem=sem)


@app.route('/admin/report/scholarship')
def scholarship_report():
    if not has_role('администратор'):
        return redirect('/login')
    gid = request.args.get('group_id', '')
    sem = request.args.get('semester', '')

    conn = db()
    group = conn.execute('SELECT * FROM groups WHERE id=?', (gid,)).fetchone() if gid else None

    sql = '''SELECT s.id, s.fio, g.name AS group_name, ROUND(AVG(gr.grade),2) AS avg_grade
             FROM students s JOIN groups g ON g.id=s.group_id
             JOIN grades gr ON gr.student_id=s.id WHERE 1=1'''
    params = []
    if gid:
        sql += ' AND s.group_id=?'
        params.append(gid)
    if sem:
        sql += ' AND gr.semester=?'
        params.append(sem)
    sql += ' GROUP BY s.id HAVING avg_grade >= 4.5'
    rows = conn.execute(sql, params).fetchall()

    result = []
    for r in rows:
        has_bad = conn.execute('''SELECT 1 FROM grades gr JOIN students s ON s.id=gr.student_id
                                  WHERE s.id=? AND gr.grade<=3 LIMIT 1''', (r['id'],)).fetchone()
        if not has_bad:
            result.append(r)
    conn.close()
    return render_template('scholarship_report.html', rows=result, group=group, sem=sem)


@app.route('/admin/report/quality')
def quality_report():
    if not has_role('администратор'):
        return redirect('/login')
    gid = request.args.get('group_id', '')
    sem = request.args.get('semester', '')

    conn = db()
    group = conn.execute('SELECT * FROM groups WHERE id=?', (gid,)).fetchone() if gid else None

    sql = '''SELECT COUNT(*) AS total, SUM(CASE WHEN gr.grade>=4 THEN 1 ELSE 0 END) AS good
             FROM grades gr JOIN students s ON s.id=gr.student_id WHERE 1=1'''
    params = []
    if gid:
        sql += ' AND s.group_id=?'
        params.append(gid)
    if sem:
        sql += ' AND gr.semester=?'
        params.append(sem)
    row = conn.execute(sql, params).fetchone()
    total = row['total'] or 0
    good = row['good'] or 0
    percent = round(good * 100 / total, 1) if total else 0
    conn.close()
    return render_template('quality_report.html', total=total, good=good, percent=percent,
                           group=group, sem=sem)


@app.route('/admin/student/<int:sid>')
def student_card(sid):
    if not has_role('администратор'):
        return redirect('/login')
    conn = db()
    s = conn.execute('''SELECT s.*, g.name AS group_name FROM students s
                        JOIN groups g ON g.id=s.group_id WHERE s.id=?''', (sid,)).fetchone()
    if s is None:
        conn.close()
        flash('Студент не найден')
        return redirect('/admin/students')

    grades = conn.execute('''SELECT gr.*, d.name AS disc_name, u.fio AS teacher_fio
        FROM grades gr JOIN disciplines d ON d.id=gr.discipline_id
        JOIN users u ON u.id=gr.teacher_id WHERE gr.student_id=?
        ORDER BY gr.semester, d.name''', (sid,)).fetchall()

    avg_all = conn.execute('SELECT ROUND(AVG(grade),2) AS a FROM grades WHERE student_id=?',
                           (sid,)).fetchone()['a']
    conn.close()
    return render_template('student_card.html', s=s, grades=grades,
                           avg_all=avg_all if avg_all is not None else '—')




if __name__ == '__main__':
    init_db()
    app.run(debug=True)