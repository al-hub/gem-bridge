import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json',
    scopes=['https://www.googleapis.com/auth/drive'],
    redirect_uri='http://localhost:8080/'
)

auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
print('\n' + '='*70)
print('1. 아래 URL을 복사하여 Windows 브라우저 주소창에 붙여넣고 로그인하세요:')
print(auth_url)
print('='*70 + '\n')

print('2. 브라우저에서 승인 후 "연결할 수 없음" 또는 빈 화면이 뜨면,')
redirected_url = input('   브라우저 주소창의 전체 URL(http://localhost:8080/?state=...&code=...)을 여기에 붙여넣고 엔터:\n> ').strip()

flow.fetch_token(authorization_response=redirected_url)

with open('token.json', 'w') as f:
    f.write(flow.credentials.to_json())

print('\nSUCCESS: token.json 발급 완료!')
