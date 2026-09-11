import { defineUniPages } from '@uni-helper/vite-plugin-uni-pages'

export default defineUniPages({
  globalStyle: {
    navigationStyle: 'custom',
    navigationBarTitleText: '',
    navigationBarBackgroundColor: '#f7f6f1',
    navigationBarTextStyle: 'black',
    backgroundColor: '#f7f6f1',
  },
  easycom: {
    autoscan: true,
    custom: {
      '^fg-(.*)': '@/components/fg-$1/fg-$1.vue',
      '^(?!z-paging-refresh|z-paging-load-more)z-paging(.*)':
        'z-paging/components/z-paging$1/z-paging$1.vue',
      '^t-(.*)': '@tdesign/uniapp/$1/$1.vue',
    },
  },
})
