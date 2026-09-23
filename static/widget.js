(() => {
  const root = document.querySelector('#ekt-assistant');
  if (!root) return;
  const copy = {
    ru: {title:'Помощник по каталогу', subtitle:'Отвечает по данным каталога', demo:'ДЕМО · данные вымышлены',
      greeting:'Здравствуйте! Помогу найти товар, проверить характеристики и остаток или объяснить условия покупки. Что ищете?',
      placeholder:'Напишите вопрос или артикул…', send:'Отправить', choose:'Выбрать', qty:'Кол-во',
      available:'В наличии', absent:'Нет в наличии', specs:'Характеристики', cert:'Сертификат',
      noCert:'Сертификат: уточните у менеджера', alternatives:'Возможные аналоги',
      confirm:'Да, добавить', cancel:'Отмена', confirmTitle:'Проверьте перед добавлением',
      total:'За', cart:'Корзина', checkout:'Открыть демо-корзину',
      error:'Связь с помощником временно недоступна. Попробуйте ещё раз.',
      privacy:'Не отправляйте данные карты или пароль в чат. Используйте защищённую страницу оформления заказа.',
      chips:['Автомат C16','Лампа E27','Условия доставки'], close:'Закрыть чат', open:'Открыть чат'},
    kk: {title:'Каталог көмекшісі', subtitle:'Каталог деректері бойынша жауап береді', demo:'ДЕМО · деректер үлгі үшін',
      greeting:'Сәлеметсіз бе! Тауарды табуға, сипаттамасы мен қалдығын тексеруге немесе сатып алу шарттарын түсіндіруге көмектесемін. Не іздейсіз?',
      placeholder:'Сұрақты немесе артикулды жазыңыз…', send:'Жіберу', choose:'Таңдау', qty:'Саны',
      available:'Қоймада бар', absent:'Қоймада жоқ', specs:'Сипаттамалар', cert:'Сертификат',
      noCert:'Сертификат: менеджерден сұраңыз', alternatives:'Ықтимал баламалар',
      confirm:'Иә, қосу', cancel:'Болдырмау', confirmTitle:'Қоспас бұрын тексеріңіз',
      total:'Бағасы', cart:'Себет', checkout:'Демо-себетті ашу',
      error:'Көмекшімен байланыс уақытша жоқ. Қайта көріңіз.',
      privacy:'Карта деректерін немесе құпия сөзді чатқа жібермеңіз. Қорғалған тапсырыс бетіне өтіңіз.',
      chips:['C16 автомат','E27 шам','Жеткізу шарттары'], close:'Чатты жабу', open:'Чатты ашу'}
  };
  const specLabels = {
    current:['Номинальный ток','Номиналды ток'], poles:['Полюса','Полюстер'],
    curve:['Кривая','Сипаттама қисығы'], breaking_capacity:['Отключающая способность','Ажырату қабілеті'],
    socket:['Цоколь','Цоколь'], power:['Мощность','Қуаты'], color_temperature:['Цветовая температура','Түс температурасы']
  };
  const state = {locale:'ru', cart:[], busy:false, open:innerWidth > 800, pending:null};
  const el = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const label = key => copy[state.locale][key];
  const money = value => `${Number(value).toLocaleString(state.locale === 'kk' ? 'kk-KZ' : 'ru-KZ')} ₸`;
  const hasSensitive = value => {
    if (/cvv|cvc|password|card\s*number|номер\s*карты|пароль|құпия\s*сөз|карта\s*нөмірі/i.test(value)) return true;
    for (const match of value.matchAll(/(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)/g)) {
      const digits = [...match[0].replace(/\D/g,'')].reverse().map(Number);
      if (digits.length >= 13 && digits.length <= 19 && digits.reduce((sum,d,i) =>
        sum + (i % 2 ? (d * 2 > 9 ? d * 2 - 9 : d * 2) : d),0) % 10 === 0) return true;
    }
    return false;
  };
  const safeLink = (url, title) => {
    try {
      const parsed = new URL(url, location.origin);
      if (parsed.protocol !== 'https:' && parsed.origin !== location.origin) return null;
      const link = el('a','source-link',title + ' ↗');
      link.href = parsed.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      return link;
    } catch (_) { return null; }
  };

  const launch = el('button','chat-launch');
  launch.type = 'button';
  launch.setAttribute('aria-label',label('open'));
  const launchIcon = el('span','launch-icon','✦');
  const launchText = el('span','launch-text','Спросить о товаре');
  launch.append(launchIcon,launchText);
  const panel = el('section','chat-panel');
  panel.setAttribute('role','dialog');
  panel.setAttribute('aria-label',label('title'));
  const head = el('header','chat-head');
  const avatar = el('div','assistant-avatar','e');
  const titles = el('div','chat-titles');
  const title = el('strong','',label('title'));
  const subtitle = el('span','',label('subtitle'));
  titles.append(title,subtitle);
  const lang = el('button','lang-switch','ҚАЗ');
  lang.type = 'button';
  lang.setAttribute('aria-label','Русский / Қазақша');
  const close = el('button','close-button','×');
  close.type = 'button';
  close.setAttribute('aria-label',label('close'));
  head.append(avatar,titles,lang,close);
  const banner = el('div','demo-banner',label('demo'));
  const messages = el('div','messages');
  messages.setAttribute('role','log');
  messages.setAttribute('aria-live','polite');
  const suggestions = el('div','suggestions');
  const form = el('form','composer');
  const input = el('input','message-input');
  input.name = 'message';
  input.placeholder = label('placeholder');
  input.maxLength = 1000;
  input.autocomplete = 'off';
  input.setAttribute('aria-label',label('placeholder'));
  const send = el('button','send-button','↑');
  send.type = 'submit';
  send.setAttribute('aria-label',label('send'));
  form.append(input,send);
  const footer = el('div','chat-footer');
  const footerText = el('span','',label('cart')+' · 0');
  const footerLink = el('a','','↗');
  footerLink.href = '/demo-cart';
  footerLink.setAttribute('aria-label',label('checkout'));
  footer.append(footerText, footerLink);
  panel.append(head,banner,messages,suggestions,form,footer);
  root.append(launch,panel);

  function updateLabels() {
    document.documentElement.lang = state.locale;
    title.textContent = label('title');
    subtitle.textContent = label('subtitle');
    banner.textContent = label('demo');
    lang.textContent = state.locale === 'ru' ? 'ҚАЗ' : 'РУС';
    input.placeholder = label('placeholder');
    input.setAttribute('aria-label',label('placeholder'));
    send.setAttribute('aria-label',label('send'));
    close.setAttribute('aria-label',label('close'));
    launch.setAttribute('aria-label',label('open'));
    launchText.textContent = state.locale === 'ru' ? 'Спросить о товаре' : 'Тауарды сұрау';
    footerLink.setAttribute('aria-label',label('checkout'));
    updateCart(state.cart);
    suggestions.replaceChildren();
    for (const text of label('chips')) {
      const button = el('button','suggestion',text);
      button.type = 'button';
      button.addEventListener('click', () => sendMessage(text));
      suggestions.append(button);
    }
  }
  function updateCart(items) {
    state.cart = items;
    const count = items.reduce((n,item) => n + item.quantity,0);
    footerText.textContent = `${label('cart')} · ${count}`;
  }
  function setOpen(open) {
    state.open = open;
    panel.classList.toggle('is-open',open);
    launch.classList.toggle('is-hidden',open);
    if (open) input.focus();
  }
  function scrollDown() { messages.scrollTop = messages.scrollHeight; }
  function bubble(text, mine=false) {
    const row = el('div',mine ? 'message-row mine' : 'message-row');
    if (!mine) row.append(el('div','message-avatar','e'));
    row.append(el('div','bubble',text));
    messages.append(row);
    scrollDown();
  }
  function clearPendingUI() {
    for (const node of messages.querySelectorAll('.pending-panel')) node.remove();
    state.pending = null;
  }
  function renderProduct(product) {
    const card = el('article','product-card');
    const top = el('div','product-top');
    top.append(el('span','sku',product.sku),el('span',product.stock ? 'stock in-stock' : 'stock out-stock',
      product.stock ? `${label('available')} · ${product.stock}` : label('absent')));
    card.append(top,el('h3','',product.name),el('div','price',money(product.price_kzt)));
    const spec = el('div','spec-list');
    for (const [key,value] of Object.entries(product.specifications || {})) {
      const row = el('div','spec-row');
      row.append(el('span','',specLabels[key] ? specLabels[key][state.locale === 'kk' ? 1 : 0] : key),
                 el('b','',value));
      spec.append(row);
    }
    card.append(spec);
    if (product.certificate_url) {
      const link = safeLink(product.certificate_url,label('cert'));
      if (link) card.append(link);
    } else card.append(el('span','no-certificate',label('noCert')));
    if (product.stock) {
      const action = el('div','card-action');
      const qtyWrap = el('label','qty-label',label('qty'));
      const qty = el('input','qty-input');
      qty.type = 'number'; qty.min = '1'; qty.max = String(product.stock); qty.value = '1';
      qtyWrap.append(qty);
      const choose = el('button','choose-button',label('choose'));
      choose.type = 'button';
      choose.addEventListener('click', async () => {
        const quantity = Number(qty.value);
        if (!Number.isSafeInteger(quantity) || quantity < 1) { qty.focus(); return; }
        await perform('/api/cart/propose',{sku:product.sku,quantity,locale:state.locale});
      });
      action.append(qtyWrap,choose);
      card.append(action);
    }
    return card;
  }
  function renderPending(pending) {
    state.pending = pending;
    const box = el('div','pending-panel');
    box.append(el('div','pending-heading',label('confirmTitle')),
      el('strong','pending-name',`${pending.name} · ${pending.sku}`),
      el('div','pending-count',`${pending.quantity} × ${money(pending.price_kzt)} = ${money(pending.quantity * pending.price_kzt)}`));
    const actions = el('div','pending-actions');
    const confirm = el('button','confirm-button',label('confirm'));
    confirm.type = 'button';
    confirm.addEventListener('click', async () => {
      bubble(label('confirm'),true);
      await perform('/api/cart/confirm',{pending_id:pending.id,locale:state.locale});
    });
    const cancel = el('button','cancel-button',label('cancel'));
    cancel.type = 'button';
    cancel.addEventListener('click',() => sendMessage(state.locale === 'kk' ? 'Жоқ' : 'Нет'));
    actions.append(confirm,cancel); box.append(actions); messages.append(box);
    scrollDown();
  }
  function renderAnswer(data) {
    clearPendingUI();
    state.locale = data.locale || state.locale;
    updateLabels();
    bubble(data.reply);
    for (const product of data.products || []) messages.append(renderProduct(product));
    if (data.alternatives?.length) {
      const heading = el('div','section-title',label('alternatives'));
      messages.append(heading);
      if (data.alternative_reason) messages.append(el('div','alternative-reason',data.alternative_reason));
      for (const product of data.alternatives) messages.append(renderProduct(product));
    }
    for (const source of data.sources || []) {
      const link = safeLink(source.url,source.label);
      if (link) messages.append(link);
    }
    if (data.pending) renderPending(data.pending);
    if (data.checkout_url) {
      const link = safeLink(data.checkout_url,label('checkout'));
      if (link) { link.classList.add('checkout-link'); messages.append(link); }
    }
    updateCart(data.cart || []);
    scrollDown();
  }
  async function perform(path,body) {
    if (state.busy) return;
    state.busy = true;
    send.disabled = true;
    try {
      const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
        credentials:'same-origin',body:JSON.stringify(body)});
      if (!response.ok) throw new Error(String(response.status));
      renderAnswer(await response.json());
    } catch (_) { bubble(label('error')); }
    finally { state.busy = false; send.disabled = false; input.focus(); }
  }
  async function sendMessage(text) {
    if (state.busy || !text.trim()) return;
    if (hasSensitive(text)) { input.value = ''; bubble(label('privacy')); return; }
    bubble(text.trim(),true);
    input.value = '';
    await perform('/api/chat',{message:text.trim(),locale:state.locale});
  }
  launch.addEventListener('click',() => setOpen(true));
  close.addEventListener('click',() => setOpen(false));
  lang.addEventListener('click',() => {
    state.locale = state.locale === 'ru' ? 'kk' : 'ru';
    updateLabels();
  });
  form.addEventListener('submit',event => { event.preventDefault(); sendMessage(input.value); });
  updateLabels();
  bubble(label('greeting'));
  setOpen(state.open);
  fetch('/api/bootstrap',{credentials:'same-origin'}).then(r => r.json()).then(data => {
    updateCart(data.cart || []);
    if (data.pending) renderPending(data.pending);
  }).catch(() => {});
})();
