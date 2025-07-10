<script setup>
import TreeSelect, {LOAD_CHILDREN_OPTIONS, LOAD_ROOT_OPTIONS} from 'vue3-treeselect'
import 'vue3-treeselect/dist/vue3-treeselect.css'
import {ref} from 'vue'
import {fetchDirectories} from "../utils.js"

const reloadKey = ref(0)
const triggerReload = () => {
  reloadKey.value += 1
}


const props = defineProps({
  inputName: {
    type: String,
    required: true
  },
  baseUrl: {
    type: String,
    required: true
  },
  initialValue: {
    type: String,
    required: false
  },
  initialConnectionId: {
    type: String,
    required: true
  },
  connectionInputEl: {
    type: Object,
    required: true,
  }
});

const options = ref(null)

// Reactive value and options
const value = ref(props.initialValue || null)
const connectionId = ref(props.initialConnectionId)

props.connectionInputEl.addEventListener('change', (e) => {
  const newValue = e.target.value;
  if (newValue !== connectionId.value) {
    connectionId.value = newValue;
    value.value = null; // Reset the value when the connection changes
    options.value = null; // Reset options to trigger reload
  }

  triggerReload(); // Trigger reload to fetch new options
})

const loadOptions = async ({action, parentNode, callback}) => {
  if (action === LOAD_ROOT_OPTIONS) {
    if (!connectionId.value) {
      callback(new Error('No connection ID provided'), null);
      return;
    }
    try {

      const directories = await fetchDirectories(props.baseUrl, connectionId.value, {
        rootRequest: true,
        remotePath: props.initialValue,
      })

      options.value = directories


    } catch (error) {
      console.error('Error fetching options:', error);
      callback(error, null)
    }

  } else if (action === LOAD_CHILDREN_OPTIONS) {
    const remotePath = parentNode.id
    try {
      parentNode.children = await fetchDirectories(props.baseUrl, connectionId.value, {remotePath})

    } catch (error) {
      console.error('Error fetching options:', error);
      callback(error, null)
    }
  }
}


</script>

<template>
  <div id="app">
    <TreeSelect
        :key="reloadKey"
        :name="props.inputName"
        v-model="value"
        :options="options"
        :load-options="loadOptions"
    />
  </div>
</template>


<style>
.vue-treeselect--single .vue-treeselect__input {
  min-height: 20px;
  border: none;
  padding: 0;
}

.vue-treeselect--single .vue-treeselect__input:hover {
  border: none;
}

.vue-treeselect--single .vue-treeselect__input:focus-visible {
  outline: none !important;
}

</style>
