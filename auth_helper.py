import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/tasks'
]

flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json',
    scopes=SCOPES,
    redirect_uri='http://localhost:8080/'
)

auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
print('\n' + '='*75)
print('🚀 [gem-bridge] Google Drive + Google Tasks 통합 인증 마법사')
print('='*75)
print('1. 아래 URL을 복사하여 Windows 브라우저 주소창에 붙여넣고 Google 계정으로 로그인하세요:')
print(auth_url)
print('='*75 + '\n')

print('2. 브라우저에서 권한(Drive 및 Tasks)을 모두 허용한 후,')
print('   "연결할 수 없음" 또는 빈 화면이 뜨면 주소창의 전체 URL을 복사해 주세요.')
redirected_url = input('   브라우저 주소창의 전체 URL(http://localhost:8080/?state=...&code=...)을 여기에 붙여넣고 엔터:\n> ').strip()

flow.fetch_token(authorization_response=redirected_url)

with open('token.json', 'w', encoding='utf-8') as f:
    f.write(flow.credentials.to_json())

print('\n🎉 SUCCESS: Google Drive + Google Tasks 권한이 포함된 token.json 발급 완료!')
print('   이제 모바일 Gemini에서 "@Google Tasks 작업 등록해줘"로 0-Tap 개발이 가능합니다.')
