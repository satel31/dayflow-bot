const $ = (selector) => document.querySelector(selector);
const initialDate = new Date().toISOString().slice(0, 10);
const state = { date: initialDate, taskDate: initialDate, activeTaskList: '', duration: 60, view: 'day', events: new Map(), allTasks: [], visibleTasks: [], editingEventId: null, assistantToken: '', colors: {}, defaultColor: '#e85d3f', groups: [], tasklists: [], groupTargetSelect: null, editingGroupName: null, groupManuallyChanged: false, suggestTimer: null };
const fmtTime = (iso) => new Intl.DateTimeFormat('ru-RU', {hour: '2-digit', minute: '2-digit'}).format(new Date(iso));
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const classicEventColors = {'1':'#a4bdfc','2':'#7ae7bf','3':'#dbadff','4':'#ff887c','5':'#fbd75b','6':'#ffb878','7':'#46d6db','8':'#e1e1e1','9':'#5484ed','10':'#51b749','11':'#dc2127'};
const eventColorStyle = (event) => `--event-color:${classicEventColors[String(event.color)] || state.defaultColor}`;
const groupOptions = (selected = '') => `<option value="">Без группы</option>${state.groups.map(group => `<option value="${escapeHtml(group.name)}"${group.name === selected ? ' selected' : ''}>${escapeHtml(group.name)}</option>`).join('')}`;

async function loadGroups() {
  try {
    const data = await api('/api/event-groups');
    state.groups = data.groups || [];
    $('#eventGroup').innerHTML = groupOptions();
    $('#groupsOverview').innerHTML = state.groups.length
      ? state.groups.map(group => `<span class="group-chip"><span class="group-dot" style="--group-color:${classicEventColors[group.color] || state.defaultColor}"></span>${escapeHtml(group.name)}</span>`).join('')
      : '<span class="empty">Групп пока нет</span>';
  } catch (_) { state.groups = []; }
}

function renderGroupManager() {
  const colors = classicEventColors;
  const ids = Object.keys(colors).sort((left, right) => Number(left) - Number(right));
  const selected = $('#groupColor').value || ids[0] || '1';
  $('#groupColor').value = selected;
  $('#groupColorPalette').innerHTML = ids.map(id => `<button type="button" class="color-choice${id === selected ? ' selected' : ''}" data-color-id="${id}" style="--choice-color:${colors[id]}" aria-label="Цвет ${id}" title="${colors[id]}"></button>`).join('');
  document.querySelectorAll('.color-choice').forEach(button => button.onclick = () => {
    $('#groupColor').value = button.dataset.colorId;
    document.querySelectorAll('.color-choice').forEach(item => item.classList.toggle('selected', item === button));
  });
  $('#existingGroups').innerHTML = state.groups.length
    ? state.groups.map(group => `<div class="existing-group"><span class="group-dot" style="--group-color:${colors[group.color] || state.defaultColor}"></span><strong>${escapeHtml(group.name)}</strong><button type="button" class="edit-group" data-group-name="${escapeHtml(group.name)}">Изменить</button><button type="button" class="delete-group" data-group-name="${escapeHtml(group.name)}">Удалить</button></div>`).join('')
    : '<div class="empty">Групп пока нет</div>';
  document.querySelectorAll('.delete-group').forEach(button => button.onclick = async () => {
    const name = button.dataset.groupName;
    if (!confirm(`Удалить группу «${name}»? Существующие события сохранят свой цвет.`)) return;
    try {
      await api(`/api/event-groups/${encodeURIComponent(name)}`, {method:'DELETE'});
      await loadGroups();
      if (state.groupTargetSelect && state.groupTargetSelect !== $('#eventGroup')) state.groupTargetSelect.innerHTML = groupOptions();
      renderGroupManager();
    } catch (error) { notice(error.message); }
  });
  document.querySelectorAll('.edit-group').forEach(button => button.onclick = () => {
    const group = state.groups.find(item => item.name === button.dataset.groupName);
    openGroupDialog(state.groupTargetSelect, group);
  });
}

function openGroupDialog(targetSelect = null, group = null) {
  state.groupTargetSelect = targetSelect;
  state.editingGroupName = group?.name || null;
  $('#groupForm').reset();
  $('#groupForm').elements.name.value = group?.name || '';
  $('#groupColor').value = group?.color || '';
  $('#groupDialogEyebrow').textContent = group ? 'РЕДАКТИРОВАНИЕ ГРУППЫ' : 'НОВАЯ ГРУППА';
  $('#groupDialogTitle').textContent = group ? 'Изменить группу' : 'Как её назовём?';
  $('#saveGroup').textContent = group ? 'Сохранить' : 'Создать группу';
  renderGroupManager();
  if (!$('#groupDialog').open) $('#groupDialog').showModal();
}

async function loadTasklists() {
  try {
    const data = await api('/api/tasklists');
    state.tasklists = data.tasklists || [];
    $('#taskList').innerHTML = state.tasklists.length
      ? state.tasklists.map((list, index) => `<option value="${escapeHtml(list.title)}"${index === 0 ? ' selected' : ''}>${escapeHtml(list.title)}</option>`).join('')
      : '<option value="">Списков нет</option>';
    $('#taskListFilter').innerHTML = '<option value="">Все списки</option>' + state.tasklists.map(list => `<option value="${escapeHtml(list.title)}">${escapeHtml(list.title)}</option>`).join('');
  } catch (error) {
    $('#taskList').innerHTML = '<option value="">Не удалось загрузить списки</option>';
  }
}

function updateGreeting() {
  const hour = new Date().getHours();
  $('#greeting').textContent = hour < 5
    ? 'Доброй ночи'
    : hour < 12
      ? 'Доброе утро'
      : hour < 18
        ? 'Добрый день'
        : 'Добрый вечер';
}

function notice(text = '', needsGoogle = false) {
  $('#noticeText').textContent = text;
  $('#notice').classList.toggle('hidden', !text);
  $('#connectGoogle').classList.toggle('hidden', !needsGoogle);
}

async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || 'Не удалось загрузить данные');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function renderTasks(tasks, emptyText) {
  state.allTasks = tasks;
  tasks = state.activeTaskList ? tasks.filter(task => task.list === state.activeTaskList) : tasks;
  state.visibleTasks = tasks;
  $('#taskCount').textContent = tasks.length;
  const grouped = tasks.reduce((result, task) => {
    const list = task.list || 'Без списка';
    if (!result.has(list)) result.set(list, []);
    result.get(list).push(task);
    return result;
  }, new Map());
  const localDateKey = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const today = new Date();
  const todayKey = localDateKey(today);
  const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
  const tomorrowKey = localDateKey(tomorrow);
  const renderTask = (task, childrenByParent, depth = 0) => {
    const children = childrenByParent.get(task.id) || [];
    return `<div class="task${depth ? ' task-child' : ''}" data-task-id="${escapeHtml(task.id)}" data-task-list="${escapeHtml(task.list)}"><button title="Выполнить" data-complete-task aria-label="Выполнить"></button><div><strong>${escapeHtml(task.title)}</strong>${task.notes ? `<small>${escapeHtml(task.notes)}</small>` : ''}</div></div>${children.map(child => renderTask(child, childrenByParent, depth + 1)).join('')}`;
  };
  const renderDay = (due, items, childrenByParent) => {
    let label = 'Без даты';
    let overdue = false;
    if (due) {
      const value = new Date(`${due}T12:00:00`);
      const formatted = value.toLocaleDateString('ru-RU', {weekday:'short', day:'numeric', month:'long'});
      if (due === todayKey) label = `Сегодня · ${formatted}`;
      else if (due === tomorrowKey) label = `Завтра · ${formatted}`;
      else { label = formatted; overdue = due < todayKey; }
    }
    return `<section class="task-day"><h4 class="task-day-title${overdue ? ' overdue' : ''}">${overdue ? 'Просрочено · ' : ''}${label}</h4>${items.map(task => renderTask(task, childrenByParent)).join('')}</section>`;
  };
  const renderList = ([list, items]) => {
    items.sort((left, right) => String(left.position || '').localeCompare(String(right.position || '')));
    const itemIds = new Set(items.map(task => task.id));
    const childrenByParent = items.reduce((result, task) => {
      if (task.parent && itemIds.has(task.parent)) {
        if (!result.has(task.parent)) result.set(task.parent, []);
        result.get(task.parent).push(task);
      }
      return result;
    }, new Map());
    const roots = items.filter(task => !task.parent || !itemIds.has(task.parent));
    const days = roots.reduce((result, task) => {
      const due = task.due ? task.due.slice(0, 10) : '';
      if (!result.has(due)) result.set(due, []);
      result.get(due).push(task);
      return result;
    }, new Map());
    const orderedDays = [...days.entries()].sort(([left], [right]) => !left ? 1 : !right ? -1 : left.localeCompare(right));
    return `<section class="task-group"><h3 class="task-group-title">${escapeHtml(list)}<span>${items.length}</span></h3><div class="task-days">${orderedDays.map(([due, dayItems]) => renderDay(due, dayItems, childrenByParent)).join('')}</div></section>`;
  };
  $('#tasks').innerHTML = tasks.length
    ? `<div class="task-groups">${[...grouped.entries()].map(renderList).join('')}</div>`
    : `<div class="empty">${emptyText}</div>`;
  document.querySelectorAll('.task').forEach(element => {
    element.onclick = () => openTaskEditor(element, tasks.find(task => task.id === element.dataset.taskId && task.list === element.dataset.taskList));
    const complete = element.querySelector('[data-complete-task]');
    complete.onclick = click => { click.stopPropagation(); completeTask(element.dataset.taskId, element.dataset.taskList, complete); };
  });
}

function openTaskEditor(element, task) {
  if (!task || document.querySelector('.task-edit-form')) return;
  element.innerHTML = `<form class="task-edit-form"><div class="task-edit-row"><input name="title" value="${escapeHtml(task.title)}" required maxlength="300"><input name="due_date" type="date" value="${task.due ? task.due.slice(0, 10) : ''}"></div><textarea name="notes" rows="2" placeholder="Заметка">${escapeHtml(task.notes || '')}</textarea><div class="task-edit-actions"><button type="button" class="task-delete">Удалить</button><button type="button" class="task-cancel">Отмена</button><button type="submit" class="task-save">Сохранить</button></div></form>`;
  const form = element.querySelector('form');
  form.onclick = click => click.stopPropagation();
  form.elements.title.focus();
  form.querySelector('.task-cancel').onclick = () => renderTasks(state.allTasks, 'Задач нет.');
  form.querySelector('.task-delete').onclick = async () => {
    if (!confirm('Удалить эту задачу?')) return;
    try { await api(`/api/tasks/${encodeURIComponent(task.list)}/${encodeURIComponent(task.id)}`, {method:'DELETE'}); await loadTasksForDate(); }
    catch (error) { notice(error.message, error.status === 401); }
  };
  form.onsubmit = async submit => {
    submit.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    if (!payload.due_date) payload.due_date = null;
    try { await api(`/api/tasks/${encodeURIComponent(task.list)}/${encodeURIComponent(task.id)}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); await loadTasksForDate(); }
    catch (error) { notice(error.message, error.status === 401); }
  };
}

async function loadTasksForDate() {
  try {
    const data = await api(`/api/tasks?target=${state.taskDate}`);
    renderTasks(data.tasks, 'К этой дате задач нет.');
  } catch (error) { notice(error.message, error.status === 401); }
}

function bindEventEditors(events) {
  events.forEach(event => state.events.set(event.id, event));
  document.querySelectorAll('[data-event-id]').forEach(element => {
    element.onclick = () => openInlineEventEditor(element, state.events.get(element.dataset.eventId));
    element.querySelector('a')?.addEventListener('click', click => click.stopPropagation());
  });
}

function openInlineEventEditor(element, event) {
  if (!event || element.classList.contains('is-editing')) return;
  if (document.querySelector('.is-editing')) return;
  element.classList.add('is-editing');
  const start = new Date(event.start);
  const end = new Date(event.end);
  const duration = Math.round((end - start) / 60000);
  const durations = [30, 60, 90, 120];
  if (!durations.includes(duration)) durations.push(duration);
  element.innerHTML = `<form class="inline-event-form">
    <input name="title" value="${escapeHtml(event.title)}" required maxlength="300" aria-label="Название">
    <input name="event_date" type="date" value="${event.start.slice(0, 10)}" required aria-label="Дата">
    <input name="start_time" type="time" value="${event.start.slice(11, 16)}" required aria-label="Время">
    <select name="duration_minutes" aria-label="Продолжительность">${durations.sort((a,b) => a-b).map(value => `<option value="${value}"${value === duration ? ' selected' : ''}>${value} мин</option>`).join('')}</select>
    <div class="inline-group-row"><select name="group_name" aria-label="Группа">${groupOptions(event.group || '')}</select><button type="button" class="inline-add-group">＋ Новая группа</button></div>
    <div class="inline-event-actions"><button type="button" class="inline-delete">Удалить</button><button type="button" class="inline-cancel">Отмена</button><button type="submit" class="inline-save">Сохранить</button></div>
  </form>`;
  const form = element.querySelector('form');
  form.onclick = click => click.stopPropagation();
  form.elements.title.focus();
  form.querySelector('.inline-cancel').onclick = () => loadCurrentView();
  form.querySelector('.inline-add-group').onclick = () => {
    openGroupDialog(form.elements.group_name);
  };
  form.querySelector('.inline-delete').onclick = async () => {
    if (!confirm('Удалить это событие?')) return;
    try {
      await api(`/api/events/${encodeURIComponent(event.id)}`, {method: 'DELETE'});
      await loadCurrentView(); await loadSlots();
    } catch (error) { notice(error.message, error.status === 401); }
  };
  form.onsubmit = async submit => {
    submit.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.duration_minutes = Number(payload.duration_minutes);
    try {
      await api(`/api/events/${encodeURIComponent(event.id)}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      await loadCurrentView(); await loadSlots();
    } catch (error) { notice(error.message, error.status === 401); }
  };
}

function openEventDialog(event = null) {
  const form = $('#eventForm');
  form.reset();
  state.editingEventId = event?.id || null;
  if (event) {
    const start = new Date(event.start);
    const end = new Date(event.end);
    form.elements.title.value = event.title;
    form.elements.event_date.value = event.start.slice(0, 10);
    form.elements.start_time.value = event.start.slice(11, 16);
    form.elements.duration_minutes.value = String(Math.round((end - start) / 60000));
    $('#eventDialogEyebrow').textContent = 'РЕДАКТИРОВАНИЕ';
    $('#eventDialogTitle').textContent = 'Изменить событие';
    $('#saveEvent').textContent = 'Сохранить';
    $('#deleteEvent').classList.remove('hidden');
  } else {
    const nextHour = new Date();
    nextHour.setHours(nextHour.getHours() + 1, 0, 0, 0);
    form.elements.event_date.value = state.date;
    form.elements.start_time.value = `${String(nextHour.getHours()).padStart(2, '0')}:00`;
    $('#eventDialogEyebrow').textContent = 'НОВОЕ СОБЫТИЕ';
    $('#eventDialogTitle').textContent = 'Что планируем?';
    $('#saveEvent').textContent = 'Добавить событие';
    $('#deleteEvent').classList.add('hidden');
    state.groupManuallyChanged = false;
    $('#groupPrediction').textContent = 'Группа подберётся по названию';
  }
  $('#eventDialog').showModal();
}

async function loadDay() {
  notice();
  try {
    const data = await api(`/api/today?target=${state.date}`);
    state.colors = data.colors || {};
    state.defaultColor = data.default_color || '#e85d3f';
    const human = new Intl.DateTimeFormat('ru-RU', {weekday:'long', day:'numeric', month:'long'}).format(new Date(`${state.date}T12:00:00`));
    $('#dateLabel').textContent = human.toUpperCase();
    $('#summary').textContent = `${data.events.length} событий · ${data.tasks.length} задач требуют внимания`;
    $('#events').className = 'timeline';
    $('#events').innerHTML = data.events.length
      ? data.events.map(event => `<div class="event event-edit" style="${eventColorStyle(event)}" data-event-id="${escapeHtml(event.id)}"><div class="event-time">${fmtTime(event.start)}</div><div class="event-line"></div><div class="event-body"><strong class="event-title">${escapeHtml(event.title)}</strong><small>${fmtTime(event.start)}–${fmtTime(event.end)} · нажмите, чтобы изменить</small><a class="google-event-link" href="${escapeHtml(event.link)}" target="_blank" rel="noreferrer">Открыть в Google ↗</a></div></div>`).join('')
      : '<div class="empty">В календаре свободно. Красота.</div>';
    bindEventEditors(data.events);
  } catch (error) {
    notice(error.message, error.status === 401);
    $('#events').innerHTML = '<div class="empty">Нет данных</div>';
    $('#tasks').innerHTML = '<div class="empty">Нет данных</div>';
  }
}

async function loadWeek() {
  notice();
  try {
    const data = await api(`/api/week?start=${state.date}`);
    state.colors = data.colors || {};
    state.defaultColor = data.default_color || '#e85d3f';
    const start = new Date(`${data.start}T12:00:00`);
    const end = new Date(`${data.end}T12:00:00`);
    const totalEvents = data.days.reduce((sum, day) => sum + day.events.length, 0);
    $('#dateLabel').textContent = `${start.toLocaleDateString('ru-RU', {day:'numeric', month:'long'})} — ${end.toLocaleDateString('ru-RU', {day:'numeric', month:'long'})}`.toUpperCase();
    $('#summary').textContent = `${totalEvents} событий · ${data.tasks.length} задач на горизонте`;
    $('#events').className = 'week-list';
    $('#events').innerHTML = data.days.map(day => {
      const value = new Date(`${day.date}T12:00:00`);
      const isToday = day.date === new Date().toISOString().slice(0, 10);
      const events = day.events.length
        ? day.events.map(event => `<div class="week-event" style="${eventColorStyle(event)}" data-event-id="${escapeHtml(event.id)}"><time>${fmtTime(event.start)}–${fmtTime(event.end)}</time><div><strong class="event-title">${escapeHtml(event.title)}</strong><a class="google-event-link" href="${escapeHtml(event.link)}" target="_blank" rel="noreferrer">Открыть в Google ↗</a></div></div>`).join('')
        : '<div class="week-empty">Свободно</div>';
      return `<section class="week-day"><div class="week-date${isToday ? ' is-today' : ''}"><strong>${value.toLocaleDateString('ru-RU', {weekday:'short'})}</strong><span>${value.toLocaleDateString('ru-RU', {day:'numeric', month:'short'})}</span></div><div class="week-events">${events}</div></section>`;
    }).join('');
    bindEventEditors(data.days.flatMap(day => day.events));
  } catch (error) {
    notice(error.message, error.status === 401);
    $('#events').innerHTML = '<div class="empty">Нет данных</div>';
    $('#tasks').innerHTML = '<div class="empty">Нет данных</div>';
  }
}

async function loadSlots() {
  const startDate = $('#focusStartDate').value;
  const endDate = $('#focusEndDate').value || startDate;
  const startTime = $('#focusStartTime').value;
  const endTime = $('#focusEndTime').value;
  $('#slots').innerHTML = '<span class="empty">Ищу свободные окна…</span>';
  try {
    const params = new URLSearchParams({target:startDate, end_date:endDate, start_time:startTime, end_time:endTime, duration:String(state.duration)});
    const data = await api(`/api/slots?${params}`);
    const multipleDays = data.date !== data.end_date;
    $('#slots').innerHTML = data.slots.map(slot => {
      const day = multipleDays ? `${new Date(slot.start).toLocaleDateString('ru-RU', {weekday:'short', day:'numeric', month:'short'})} · ` : '';
      const latestStart = new Date(new Date(slot.end).getTime() - state.duration * 60000).toISOString();
      const exact = new Date(latestStart).getTime() <= new Date(slot.start).getTime();
      const timeText = exact
        ? `свободно в ${fmtTime(slot.start)}`
        : `можно начать с ${fmtTime(slot.start)} до ${fmtTime(latestStart)}`;
      return `<span class="slot">${day}${timeText}</span>`;
    }).join('') || '<span class="empty">Подходящих окон нет</span>';
  } catch (error) { $('#slots').innerHTML = `<span class="empty">${escapeHtml(error.message)}</span>`; }
}

async function completeTask(taskId, taskList, button) {
  button.disabled = true;
  try {
    await api(`/api/tasks/${encodeURIComponent(taskList)}/${encodeURIComponent(taskId)}/complete`, {method:'POST'});
    await loadTasksForDate();
  } catch (error) { notice(error.message); button.disabled = false; }
}

const loadCurrentView = () => state.view === 'week' ? loadWeek() : loadDay();
async function connectGoogle() {
  try {
    const data = await api('/api/google/connect');
    location.href = data.auth_url;
  } catch (error) { notice(error.message); }
}

function setView(view) {
  state.view = view;
  $('#dayView').classList.toggle('nav-active', view === 'day');
  $('#weekView').classList.toggle('nav-active', view === 'week');
  $('.timeline-panel h2').textContent = view === 'week' ? 'Неделя' : 'Расписание';
  $('#events').innerHTML = `<div class="empty">Загружаю ${view === 'week' ? 'неделю' : 'день'}…</div>`;
  loadCurrentView();
}

$('#dayPicker').value = state.date;
$('#taskDayPicker').value = state.taskDate;
$('#focusStartDate').value = state.date;
$('#focusEndDate').value = state.date;
$('#dayPicker').onchange = event => { state.date = event.target.value; loadCurrentView(); };
$('#taskDayPicker').onchange = event => { state.taskDate = event.target.value; loadTasksForDate(); };
$('#taskListFilter').onchange = event => { state.activeTaskList = event.target.value; renderTasks(state.allTasks, 'В этом списке задач нет.'); };
$('#dayView').onclick = () => setView('day');
$('#weekView').onclick = () => setView('week');
document.querySelectorAll('[data-duration]').forEach(button => button.onclick = () => {
  document.querySelector('.chips .selected').classList.remove('selected');
  button.classList.add('selected'); state.duration = Number(button.dataset.duration);
});
$('#findSlots').onclick = loadSlots;
$('#assistantForm').onsubmit = async event => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  const text = $('#assistantInput').value.trim();
  if (!text) return;
  button.disabled = true;
  button.textContent = 'Думаю…';
  $('#assistantResult').classList.remove('hidden');
  $('#assistantText').textContent = 'Разбираю запрос…';
  $('#assistantActions').classList.add('hidden');
  try {
    const result = await api('/api/assistant/plan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
    state.assistantToken = result.token || '';
    $('#assistantText').textContent = result.text;
    $('#assistantActions').classList.toggle('hidden', !result.requires_confirmation);
  } catch (error) {
    state.assistantToken = '';
    $('#assistantText').textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = 'Разобрать';
  }
};
$('#cancelAssistant').onclick = async () => {
  if (state.assistantToken) await api('/api/assistant/cancel', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:state.assistantToken})}).catch(() => {});
  state.assistantToken = '';
  $('#assistantResult').classList.add('hidden');
};
$('#confirmAssistant').onclick = async () => {
  if (!state.assistantToken) return;
  const button = $('#confirmAssistant');
  button.disabled = true;
  button.textContent = 'Выполняю…';
  try {
    const result = await api('/api/assistant/confirm', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:state.assistantToken})});
    state.assistantToken = '';
    $('#assistantText').textContent = result.text;
    $('#assistantActions').classList.add('hidden');
    $('#assistantInput').value = '';
    await Promise.all([loadCurrentView(), loadTasksForDate(), loadSlots()]);
  } catch (error) { $('#assistantText').textContent = error.message; }
  finally { button.disabled = false; button.textContent = 'Подтвердить'; }
};
$('#addButton').onclick = () => $('#taskDialog').showModal();
$('#addEventButton').onclick = () => openEventDialog();
$('#connectGoogle').onclick = connectGoogle;
$('#reconnectGoogle').onclick = connectGoogle;
$('#closeDialog').onclick = () => $('#taskDialog').close();
$('#closeEventDialog').onclick = () => $('#eventDialog').close();
$('#addGroupButton').onclick = () => openGroupDialog($('#eventGroup'));
$('#manageGroups').onclick = () => openGroupDialog();
$('#closeGroupDialog').onclick = () => { $('#groupDialog').close(); state.groupTargetSelect = null; state.editingGroupName = null; };
$('#groupForm').onsubmit = async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  try {
    const editing = state.editingGroupName;
    const url = editing ? `/api/event-groups/${encodeURIComponent(editing)}` : '/api/event-groups';
    const created = await api(url, {method:editing ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    await loadGroups();
    if (state.groupTargetSelect && state.groupTargetSelect !== $('#eventGroup')) {
      state.groupTargetSelect.innerHTML = groupOptions(created.name);
    } else {
      $('#eventGroup').value = created.name;
    }
    state.groupManuallyChanged = true;
    $('#groupPrediction').textContent = `${editing ? 'Обновлена' : 'Создана'} группа: ${created.name}`;
    $('#groupDialog').close();
    state.groupTargetSelect = null;
    state.editingGroupName = null;
    event.target.reset();
  } catch (error) { notice(error.message); }
};
$('#eventGroup').onchange = () => { state.groupManuallyChanged = true; $('#groupPrediction').textContent = ''; };
$('#eventForm').elements.title.oninput = event => {
  if (state.groupManuallyChanged) return;
  clearTimeout(state.suggestTimer);
  const title = event.target.value.trim();
  if (title.length < 2) { $('#groupPrediction').textContent = 'Группа подберётся по названию'; return; }
  $('#groupPrediction').textContent = 'Подбираю группу…';
  state.suggestTimer = setTimeout(async () => {
    try {
      const data = await api(`/api/event-groups/suggest?title=${encodeURIComponent(title)}`);
      if (state.groupManuallyChanged) return;
      $('#eventGroup').value = data.group || '';
      $('#groupPrediction').textContent = data.group ? `Предложена группа: ${data.group}` : 'Не удалось уверенно определить группу';
    } catch (_) { $('#groupPrediction').textContent = 'Выберите группу вручную'; }
  }, 550);
};
$('#deleteEvent').onclick = async () => {
  if (!state.editingEventId || !confirm('Удалить это событие?')) return;
  try {
    await api(`/api/events/${encodeURIComponent(state.editingEventId)}`, {method: 'DELETE'});
    $('#eventDialog').close();
    await loadCurrentView(); await loadSlots();
  } catch (error) { notice(error.message, error.status === 401); }
};
$('#taskForm').onsubmit = async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  if (!payload.due_date) delete payload.due_date;
  try {
    await api('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    $('#taskDialog').close(); event.target.reset(); await loadCurrentView();
  } catch (error) { notice(error.message); $('#taskDialog').close(); }
};
$('#eventForm').onsubmit = async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.duration_minutes = Number(payload.duration_minutes);
  try {
    const url = state.editingEventId ? `/api/events/${encodeURIComponent(state.editingEventId)}` : '/api/events';
    await api(url, {method: state.editingEventId ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    $('#eventDialog').close();
    event.target.reset();
    await loadCurrentView();
    await loadSlots();
  } catch (error) { notice(error.message, error.status === 401); $('#eventDialog').close(); }
};

updateGreeting(); loadGroups(); loadTasklists(); loadDay(); loadTasksForDate(); loadSlots();
