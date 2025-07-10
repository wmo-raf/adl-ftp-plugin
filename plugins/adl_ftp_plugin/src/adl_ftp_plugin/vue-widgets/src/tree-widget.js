import {createApp} from 'vue'
import DjangoUtilsPlugin from 'vue-plugin-django-utils'

import TreeWidget from './components/TreeWidget.vue'


window.initTreeWidget = (rootElementId, {
    initialValue,
    baseUrl,
    connectionInputElId,
    inputName
}) => {

    const el = document.getElementById(rootElementId)
    const connectionInputEl = document.getElementById(connectionInputElId)

    if (!el) {
        console.error(`Root Element with id ${rootElementId} not found.`)
        return
    }

    if (!connectionInputEl) {
        console.error(`Connection Input with id ${connectionInputElId} not found.`)
        return
    }

    const initialConnectionId = connectionInputEl ? connectionInputEl.value : null


    const app = createApp(TreeWidget, {
        initialValue,
        baseUrl,
        initialConnectionId,
        inputName,
        connectionInputEl
    })

    app.use(DjangoUtilsPlugin, {rootElement: el})
    app.mount(el)
}


