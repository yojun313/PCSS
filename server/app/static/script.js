document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('conference-grid');
  const selectedList = document.getElementById('selected-conferences'); // 홈페이지에 없으면 null일 수 있음
  const submitBtn = document.getElementById('submit-btn');
  const form = document.getElementById('filter-form');

  // 같은 도메인(8000)에서 서빙하면 빈 문자열로 OK
  const API_BASE = ""; 
  const API_PREFIX = "/api";

  // ---------------------------
  // 1) 학회 목록 가져오기 (FastAPI)
  // ---------------------------
  fetch(`${API_BASE}${API_PREFIX}/conferences`)
    .then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => createCheckboxes(data))
    .catch(err => console.error('Error fetching conference data:', err));

  // 체크박스 생성 함수
  function createCheckboxes(data) {
    const groupedData = data.reduce((acc, row) => {
      const kind = row.kind;
      if (!acc[kind]) acc[kind] = [];
      acc[kind].push(row.conference);
      return acc;
    }, {});

    for (const kind in groupedData) {
      const card = document.createElement('div');
      card.classList.add('card');

      const cardTitle = document.createElement('h2');
      cardTitle.textContent = kind;
      card.appendChild(cardTitle);

      const checkboxGroup = document.createElement('div');
      checkboxGroup.classList.add('checkbox-group');

      groupedData[kind].forEach(conference => {
        const label = document.createElement('label');

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.classList.add('conference-checkbox');
        checkbox.value = conference;

        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(conference));
        checkboxGroup.appendChild(label);
      });

      card.appendChild(checkboxGroup);
      grid.appendChild(card);
    }

    attachCheckboxListeners();
  }

  // 체크박스 선택 시 리스트 업데이트 (selectedList가 있는 경우만)
  function attachCheckboxListeners() {
    const checkboxes = document.querySelectorAll('.conference-checkbox');
    checkboxes.forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        if (!selectedList) return;

        selectedList.innerHTML = '';
        const selected = Array.from(checkboxes)
          .filter(cb => cb.checked)
          .map(cb => cb.value);

        selected.forEach(conf => {
          const li = document.createElement('li');
          li.textContent = conf;
          selectedList.appendChild(li);
        });
      });
    });
  }

  // ---------------------------
  // 2) 제출 버튼 클릭: FastAPI job 시작
  // ---------------------------
  submitBtn.addEventListener('click', async () => {
    const formData = new FormData(form);
    const filters = Object.fromEntries(formData.entries());

    const selectedConferences = Array.from(document.querySelectorAll('.conference-checkbox'))
      .filter(cb => cb.checked)
      .map(cb => cb.value);

    filters.selectedConferences = selectedConferences;

    // 입력값 검증
    if (!filters.option) {
      alert('옵션을 선택해주세요');
      return;
    }
    if (!filters.startyear || !filters.endyear) {
      alert('시작 연도와 종료 연도를 입력해주세요');
      return;
    }

    const startyear = parseInt(filters.startyear, 10);
    const endyear = parseInt(filters.endyear, 10);
    if (Number.isNaN(startyear) || Number.isNaN(endyear)) {
      alert('연도 입력이 올바르지 않습니다');
      return;
    }
    if (startyear > endyear) {
      alert('시작 연도는 종료 연도보다 작거나 같아야 합니다');
      return;
    }
    if (selectedConferences.length === 0) {
      alert('최소 하나 이상의 학회를 선택해주세요!');
      return;
    }

    // 타입 정리(서버 Pydantic에서 int/float 기대하면 안전)
    const payload = {
      option: String(filters.option),                 // SearchRequest가 int면 Number(...)로 바꿔도 됨
      uncertainty: Number(filters.uncertainty),
      startyear,
      endyear,
      countOption: filters.countOption ?? "no",        // UI 주석이면 기본값
      selectedConferences
    };

    try {
      // ✅ FastAPI start
      const resp = await fetch(`${API_BASE}${API_PREFIX}/search/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        throw new Error(await resp.text() || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      // data: { job_id, events_url, result_url, page_url }

      // ✅ FastAPI가 렌더하는 페이지로 이동
      window.location.href = `${API_BASE}${data.page_url}`;
    } catch (err) {
      console.error('Error:', err);
      alert('검색 시작 실패: ' + (err?.message || err));
    }
  });
});

// ---------------------------
// 3) 모두 선택/해제 버튼 (DOM 로드 후 안전하게)
// ---------------------------
document.addEventListener('DOMContentLoaded', () => {
  const toggleSelectBtn = document.getElementById('toggle-select-btn');
  if (!toggleSelectBtn) return;

  let allSelected = false;

  toggleSelectBtn.addEventListener('click', () => {
    const checkboxes = document.querySelectorAll('.conference-checkbox');
    allSelected = !allSelected;

    checkboxes.forEach(cb => { cb.checked = allSelected; });

    toggleSelectBtn.textContent = allSelected ? '모두 해제' : '모두 선택';

    // selected-conferences 리스트 갱신도 필요하면 여기서 같이
    const selectedList = document.getElementById('selected-conferences');
    if (selectedList) {
      selectedList.innerHTML = '';
      if (allSelected) {
        checkboxes.forEach(cb => {
          const li = document.createElement('li');
          li.textContent = cb.value;
          selectedList.appendChild(li);
        });
      }
    }
  });
});
